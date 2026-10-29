"""Authenticated HTTP wrapper around the controller for one local, single-process deployment.

Only a trusted gateway holding the shared secret may call this. The gateway also supplies the
learner ID (X-Learner-Id); this module checks that the learner is registered but does not
authenticate people. Responses are explicit learner projections: no answer keys, rationales,
candidate quizzes, review reports or model text. Slow agent work runs on background threads; clients poll.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hmac
import json
import os
import re
import sqlite3
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from study_buddy.controller import (SubmissionRejected, decide_next_step, dispatch_reteaching, dispatch_retry_quiz,
                                    explain_results, finish_session, get_current_quiz, get_saved_summary,
                                    get_session_status, start_quiz_session, submit_answers)
from study_buddy.controller import QuizRetrievalRejected, SessionStatusRejected, SummaryRetrievalRejected, StartRequestRejected
from study_buddy.session_flow import resume_summary
from study_buddy.summary import SummaryRejected

MAX_BODY = 8192
LEARNER_ID = re.compile(r'learner_[a-z0-9]{12}')
LESSON_ID = re.compile(r'[a-z][a-z0-9_]*')
REQUEST_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{7,127}')
RESOURCE_ID = re.compile(r'[A-Za-z0-9_-]{1,128}')
NOT_FOUND = 'Not found.'


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class Api:
    """Holds configuration and background jobs; handler methods return (status, JSON-able dict)."""

    def __init__(self, database, secret, *, max_sessions_per_day=3, agents=None, workers=2):
        if not isinstance(secret, str) or len(secret) < 32:
            raise ValueError('The API secret must be at least 32 characters.')
        if type(max_sessions_per_day) is not int or max_sessions_per_day < 1:
            raise ValueError('The daily session limit must be a positive integer.')
        self.database, self.secret, self.max_sessions_per_day = str(database), secret, max_sessions_per_day
        self.agents = dict(agents or {})  # Test seam: tutor, assessment, reviewer, revision_assessment, feedback.
        self.pool = ThreadPoolExecutor(max_workers=workers)
        self._running, self._lock = set(), threading.Lock()

    # -- plumbing -----------------------------------------------------------
    def connect(self):
        conn = sqlite3.connect(self.database, timeout=5)
        conn.execute('PRAGMA foreign_keys=ON')
        return conn

    def _background(self, key, work):
        """Run work once per key at a time; return False when that work is already running."""
        with self._lock:
            if key in self._running:
                return False
            self._running.add(key)

        def run():
            try:
                work()
            except Exception as error:  # Log the type only: provider/DB text may be sensitive.
                print(f'background job failed: {type(error).__name__}', file=sys.stderr)
            finally:
                with self._lock:
                    self._running.discard(key)
        self.pool.submit(run)
        return True

    def _is_running(self, key):
        with self._lock:
            return key in self._running

    def authenticate(self, authorization, learner_id):
        token = authorization[7:] if isinstance(authorization, str) and authorization.startswith('Bearer ') else ''
        if not hmac.compare_digest(token.encode(), self.secret.encode()):
            raise ApiError(401, 'Unauthorized.')
        if not isinstance(learner_id, str) or not LEARNER_ID.fullmatch(learner_id):
            raise ApiError(403, 'Forbidden.')
        with closing(self.connect()) as conn:
            if not conn.execute('SELECT 1 FROM learners WHERE learner_id=?', (learner_id,)).fetchone():
                raise ApiError(403, 'Forbidden.')
        return learner_id

    # -- routes -------------------------------------------------------------
    def start_session(self, learner_id, body):
        lesson_id, request_id = body.get('lesson_id'), body.get('request_id')
        if not isinstance(lesson_id, str) or not LESSON_ID.fullmatch(lesson_id):
            raise ApiError(400, 'A lesson ID is required.')
        if not isinstance(request_id, str) or not REQUEST_ID.fullmatch(request_id):
            raise ApiError(400, 'Request ID must contain 8–128 letters, digits, underscores or hyphens.')
        with closing(self.connect()) as conn:
            row = conn.execute('SELECT MAX(version) FROM lessons WHERE lesson_id=? AND approved=1', (lesson_id,)).fetchone()
            if row[0] is None:
                raise ApiError(404, 'Lesson is unavailable.')
            version = row[0]
            known = conn.execute('SELECT 1 FROM session_start_requests WHERE learner_id=? AND request_id=?',
                                 (learner_id, request_id)).fetchone()
            if not known and not self._is_running(('start', learner_id, request_id)):
                started_today = conn.execute(
                    "SELECT COUNT(*) FROM study_sessions WHERE learner_id=? AND created_at >= strftime('%Y-%m-%dT00:00:00Z','now')",
                    (learner_id,)).fetchone()[0]
                if started_today >= self.max_sessions_per_day:
                    raise ApiError(429, 'The daily session limit has been reached.')

        def work():
            with closing(self.connect()) as job_conn:
                start_quiz_session(job_conn, learner_id, lesson_id, request_id=request_id, lesson_version=version,
                                   **{k: v for k, v in self.agents.items() if k in ('tutor', 'assessment', 'reviewer', 'revision_assessment')})
        if not known:
            self._background(('start', learner_id, request_id), work)
        return 202, dict(request_id=request_id, status='starting')

    def start_request_status(self, learner_id, request_id):
        with closing(self.connect()) as conn:
            row = conn.execute('SELECT session_id FROM session_start_requests WHERE learner_id=? AND request_id=?',
                               (learner_id, request_id)).fetchone()
            if row is None:
                if self._is_running(('start', learner_id, request_id)):
                    return 200, dict(request_id=request_id, status='starting')
                raise ApiError(404, NOT_FOUND)
            return 200, self._status(conn, learner_id, row[0])

    def _status(self, conn, learner_id, session_id):
        status = get_session_status(conn, learner_id, session_id)
        status['working'] = any(self._is_running((kind, learner_id, session_id)) for kind in ('advance',))
        return status

    def session_status(self, learner_id, session_id):
        with closing(self.connect()) as conn:
            return 200, self._status(conn, learner_id, session_id)

    def current_quiz(self, learner_id, session_id):
        with closing(self.connect()) as conn:
            quiz = get_current_quiz(conn, learner_id, session_id)
        return 200, dict(session_id=quiz['session_id'], status=quiz['status'], quiz=quiz['quiz'])

    def submit(self, learner_id, quiz_id, body):
        answers = body.get('answers')
        if not isinstance(answers, dict):
            raise ApiError(400, 'Answers are required.')
        with closing(self.connect()) as conn:
            result = submit_answers(conn, learner_id, quiz_id, answers)
        graded = result.get('graded')
        if result['status'] == 'failed' or graded is None:
            return 200, dict(session_id=result['session_id'], status='failed', error=result.get('error'))
        if result.get('next_step') == 'assessment_feedback':
            self._background(('advance', learner_id, result['session_id']),
                             lambda: self._advance(learner_id, result['session_id'], result['attempt_id']))
        # Score and pass/fail only: per-question answers and rationales arrive with the saved feedback and summary.
        return 200, dict(session_id=result['session_id'], status=result['status'], score=graded['score'],
                         passed=graded['passed'], total=len(graded['items']))

    def _advance(self, learner_id, session_id, attempt_id):
        """Feedback, routing, optional reteach plus one retry quiz, or the saved summary. Mirrors session_flow."""
        feedback = {k: v for k, v in self.agents.items() if k == 'feedback'}
        with closing(self.connect()) as conn:
            explained = explain_results(conn, learner_id, attempt_id, **({'assessment': feedback['feedback']} if feedback else {}))
            if explained['status'] == 'failed':
                return
            route = decide_next_step(conn, learner_id, attempt_id)
            if route['next_step'] == 'reteach':
                taught = dispatch_reteaching(conn, learner_id, attempt_id,
                                             **({'tutor': self.agents['tutor']} if 'tutor' in self.agents else {}))
                if taught['status'] == 'failed' or taught['next_step'] != 'assessment':
                    return
                dispatch_retry_quiz(conn, learner_id, attempt_id,
                                    **{k: v for k, v in self.agents.items() if k in ('assessment', 'reviewer', 'revision_assessment')})
                return
            finish_session(conn, learner_id, session_id)

    def summary(self, learner_id, session_id):
        with closing(self.connect()) as conn:
            return 200, get_saved_summary(conn, learner_id, session_id)

    def resume(self, learner_id, session_id):
        with closing(self.connect()) as conn:
            done = resume_summary(conn, learner_id, session_id)
        return 200, dict(session_id=done['session_id'], status=done['status'], scores=done['scores'],
                         summary=done['summary'], error=done['error'])

    def route(self, method, path, learner_id, body):
        parts = [p for p in path.split('/') if p]
        for part in parts:
            if not RESOURCE_ID.fullmatch(part):
                raise ApiError(404, NOT_FOUND)
        try:
            if method == 'POST' and parts == ['sessions']:
                return self.start_session(learner_id, body)
            if method == 'GET' and len(parts) == 2 and parts[0] == 'session-requests':
                return self.start_request_status(learner_id, parts[1])
            if method == 'GET' and len(parts) == 2 and parts[0] == 'sessions':
                return self.session_status(learner_id, parts[1])
            if method == 'GET' and len(parts) == 3 and parts[0] == 'sessions' and parts[2] == 'quiz':
                return self.current_quiz(learner_id, parts[1])
            if method == 'GET' and len(parts) == 3 and parts[0] == 'sessions' and parts[2] == 'summary':
                return self.summary(learner_id, parts[1])
            if method == 'POST' and len(parts) == 3 and parts[0] == 'sessions' and parts[2] == 'resume-summary':
                return self.resume(learner_id, parts[1])
            if method == 'POST' and len(parts) == 3 and parts[0] == 'quizzes' and parts[2] == 'answers':
                return self.submit(learner_id, parts[1], body)
        except (QuizRetrievalRejected, SessionStatusRejected, SummaryRetrievalRejected, SummaryRejected):
            raise ApiError(404, NOT_FOUND) from None
        except (SubmissionRejected, StartRequestRejected) as error:
            raise ApiError(400, str(error)) from None
        raise ApiError(404, NOT_FOUND)


def make_server(api, host='127.0.0.1', port=8787):
    class Handler(BaseHTTPRequestHandler):
        server_version = 'StudyBuddy'
        sys_version = ''

        def log_message(self, *args):  # No request lines: paths contain session IDs.
            pass

        def _send(self, status, payload):
            data = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(data)

        def _handle(self, method):
            try:
                learner_id = api.authenticate(self.headers.get('Authorization'), self.headers.get('X-Learner-Id'))
                body = {}
                if method == 'POST':
                    length = int(self.headers.get('Content-Length') or 0)
                    if length > MAX_BODY:
                        raise ApiError(413, 'Request is too large.')
                    raw = self.rfile.read(length) if length else b'{}'
                    try:
                        body = json.loads(raw)
                    except ValueError:
                        raise ApiError(400, 'Request body must be JSON.') from None
                    if not isinstance(body, dict):
                        raise ApiError(400, 'Request body must be a JSON object.')
                status, payload = api.route(method, self.path.split('?')[0], learner_id, body)
            except ApiError as error:
                status, payload = error.status, dict(error=error.message)
            except Exception as error:  # Fixed message only; never echo internals.
                print(f'request failed: {type(error).__name__}', file=sys.stderr)
                status, payload = 500, dict(error='Something went wrong.')
            self._send(status, payload)

        def do_GET(self):
            self._handle('GET')

        def do_POST(self):
            self._handle('POST')

    return ThreadingHTTPServer((host, port), Handler)
