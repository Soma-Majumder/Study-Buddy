"""HTTP wrapper behaviour with fixture agents, a real SQLite file and no API calls.

These prove authentication, learner scoping, response filtering and sequencing, not AI quality.
"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
from threading import Thread
import time
import unittest
import urllib.error
import urllib.request

from study_buddy.answer_keys import get_answer_key
from study_buddy.http_api import Api, make_server
from study_buddy.lessons import load_lessons
from tests import test_session_flow
from tests.quality_fixtures import passing_review

ROOT = Path(__file__).resolve().parent.parent
SECRET = 's' * 40
LEARNER, OTHER = 'learner_a1b2c3d4e5f6', 'learner_b1b2c3d4e5f6'


class Stubs:
    """Reuse the session-flow fixture agents without importing its test cases."""
    def __init__(self):
        self.tutor_calls, self.assessment_calls, self.feedback_calls = [], [], []
    tutor = test_session_flow.SessionFlowTests.tutor
    assessment = test_session_flow.SessionFlowTests.assessment
    feedback = test_session_flow.SessionFlowTests.feedback


class HttpApiTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db = str(Path(directory.name) / 'study.db')
        with closing(sqlite3.connect(self.db)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.executescript((ROOT / 'db/schema.sql').read_text())
            load_lessons(conn)
            conn.executemany('INSERT INTO learners VALUES (?)', [(LEARNER,), (OTHER,)])
            conn.commit()
        self.stubs = Stubs()
        self.start(max_sessions_per_day=3)

    def start(self, **config):
        self.api = Api(self.db, SECRET, agents=dict(tutor=self.stubs.tutor, assessment=self.stubs.assessment,
                                                    reviewer=passing_review, feedback=self.stubs.feedback), **config)
        self.server = make_server(self.api, port=0)
        Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.addCleanup(self.api.pool.shutdown)
        self.base = f'http://127.0.0.1:{self.server.server_address[1]}'

    def call(self, method, path, body=None, *, learner=LEARNER, secret=SECRET, raw=None):
        headers = {'Content-Type': 'application/json'}
        if secret is not None:
            headers['Authorization'] = f'Bearer {secret}'
        if learner is not None:
            headers['X-Learner-Id'] = learner
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        request = urllib.request.Request(self.base + path, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            with error:
                return error.code, json.loads(error.read())

    def wait(self, check, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            value = check()
            if value:
                return value
            time.sleep(0.05)
        self.fail('Timed out waiting for background work.')

    def begin(self, request_id='request-0001', learner=LEARNER):
        status, body = self.call('POST', '/sessions', dict(lesson_id='lesson_cell_membranes', request_id=request_id), learner=learner)
        self.assertEqual((status, body['status']), (202, 'starting'))
        return self.wait(lambda: (lambda s: s[1] if s[1].get('status') == 'awaiting_answers' else None)(
            self.call('GET', f'/session-requests/{request_id}', learner=learner)))

    def answers(self, quiz_id, wrong=()):
        with closing(sqlite3.connect(self.db)) as conn:
            keys = get_answer_key(conn, quiz_id)
        return {k['question_id']: (('b' if k['correct_option'] == 'a' else 'a') if i in wrong else k['correct_option'])
                for i, k in enumerate(keys, 1)}

    def test_requests_need_the_secret_and_a_registered_learner(self):
        self.assertEqual(self.call('GET', '/sessions/abc', secret=None)[0], 401)
        self.assertEqual(self.call('GET', '/sessions/abc', secret='x' * 40)[0], 401)
        self.assertEqual(self.call('GET', '/sessions/abc', learner=None)[0], 403)
        self.assertEqual(self.call('GET', '/sessions/abc', learner='learner_zzzzzzzzzzzz')[0], 403)
        self.assertEqual(self.call('GET', '/sessions/abc', learner='../etc')[0], 403)

    def test_short_secret_is_refused(self):
        with self.assertRaises(ValueError):
            Api(self.db, 'short')

    def test_full_pass_flow_never_leaks_keys_and_saves_a_summary(self):
        status = self.begin()
        session_id = status['session_id']
        code, quiz = self.call('GET', f'/sessions/{session_id}/quiz')
        self.assertEqual(code, 200)
        text = json.dumps(quiz)
        for private in ('correct_option', 'rationale', 'answer_key'):
            self.assertNotIn(private, text)
        quiz_id = quiz['quiz']['quiz_id']
        code, graded = self.call('POST', f'/quizzes/{quiz_id}/answers', dict(answers=self.answers(quiz_id, wrong={3})))
        self.assertEqual((code, graded['score'], graded['passed']), (200, 9, True))
        self.assertNotIn('correct_option', json.dumps(graded))
        self.wait(lambda: self.call('GET', f'/sessions/{session_id}')[1]['status'] == 'completed')
        code, summary = self.call('GET', f'/sessions/{session_id}/summary')
        self.assertEqual(code, 200)
        self.assertEqual(self.stubs.feedback_calls, ['first'])

    def test_failed_first_round_reteaches_and_offers_one_retry_quiz(self):
        session_id = self.begin()['session_id']
        first = self.call('GET', f'/sessions/{session_id}/quiz')[1]['quiz']['quiz_id']
        code, graded = self.call('POST', f'/quizzes/{first}/answers', dict(answers=self.answers(first, wrong={3, 4, 6, 7})))
        self.assertEqual((code, graded['score'], graded['passed']), (200, 6, False))
        retry = self.wait(lambda: (lambda r: r[1]['quiz']['quiz_id'] if r[0] == 200 and r[1]['quiz']['quiz_id'] != first else None)(
            self.call('GET', f'/sessions/{session_id}/quiz')))
        code, again = self.call('POST', f'/quizzes/{retry}/answers', dict(answers=self.answers(retry)))
        self.assertEqual((code, again['score']), (200, 10))
        self.wait(lambda: self.call('GET', f'/sessions/{session_id}')[1]['status'] == 'completed')
        self.assertEqual(self.stubs.assessment_calls, ['first', 'retry'])

    def test_another_learner_cannot_read_or_answer(self):
        session_id = self.begin()['session_id']
        quiz_id = self.call('GET', f'/sessions/{session_id}/quiz')[1]['quiz']['quiz_id']
        self.assertEqual(self.call('GET', f'/sessions/{session_id}', learner=OTHER)[0], 404)
        self.assertEqual(self.call('GET', f'/sessions/{session_id}/quiz', learner=OTHER)[0], 404)
        self.assertEqual(self.call('GET', f'/sessions/{session_id}/summary', learner=OTHER)[0], 404)
        self.assertEqual(self.call('POST', f'/quizzes/{quiz_id}/answers', dict(answers=self.answers(quiz_id)), learner=OTHER)[0], 400)
        self.assertEqual(self.call('GET', '/session-requests/request-0001', learner=OTHER)[0], 404)

    def test_daily_limit_blocks_new_sessions_but_not_a_replay(self):
        self.server.shutdown()
        self.start(max_sessions_per_day=1)
        self.begin('request-0001')
        code, body = self.call('POST', '/sessions', dict(lesson_id='lesson_cell_membranes', request_id='request-0002'))
        self.assertEqual(code, 429)
        self.assertEqual(self.call('POST', '/sessions', dict(lesson_id='lesson_cell_membranes', request_id='request-0001'))[0], 202)
        self.assertEqual(len(self.stubs.tutor_calls), 1)

    def test_bad_input_is_rejected_without_side_effects(self):
        self.assertEqual(self.call('POST', '/sessions', dict(lesson_id='Bad Lesson', request_id='request-0001'))[0], 400)
        self.assertEqual(self.call('POST', '/sessions', dict(lesson_id='lesson_missing', request_id='request-0001'))[0], 404)
        self.assertEqual(self.call('POST', '/sessions', dict(lesson_id='lesson_cell_membranes', request_id='short'))[0], 400)
        self.assertEqual(self.call('POST', '/sessions', raw=b'not json')[0], 400)
        self.assertEqual(self.call('POST', '/sessions', raw=b'x' * 9000)[0], 413)
        self.assertEqual(self.call('GET', '/nothing')[0], 404)
        self.assertEqual(self.stubs.tutor_calls, [])


if __name__ == '__main__':
    unittest.main()
