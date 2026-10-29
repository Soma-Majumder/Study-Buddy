"""Local student UI -> existing controller -> persistent SQLite, explicit offline/live adapters.

No authentication or browser-selectable provider configuration is offered. One trusted local profile;
Host/Origin/token checks protect loopback requests, not public student accounts.
"""
import argparse
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import shlex
import shutil
import sqlite3
import sys
import tempfile
import time
from urllib.parse import urlsplit, parse_qs

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.offline_controller_fixtures import OfflineAgents, forbidden
from study_buddy.controller import (start_quiz_session, submit_answers, explain_results,
    decide_next_step, dispatch_reteaching, dispatch_retry_quiz, finish_session,
    get_session_status, get_current_quiz, get_saved_summary, run_tutor, SubmissionRejected)
from study_buddy.assessment import run_assessment, run_assessment_revision
from study_buddy.feedback_runner import run_feedback
from study_buddy.reviewer import run_reviewer
from study_buddy.answer_keys import get_client_quiz
from study_buddy.lessons import load_lessons
from study_buddy.session_start_requests import StartRequestRejected
from study_buddy.maintenance_worker import run_maintenance_cycle

LEARNER = 'learner_workbook0001'
MODE = 'real_controller_offline_fixtures'
LIVE_MODE = 'real_controller_live_agents'
LIVE_LEARNER = 'learner_workbooklive'


class WorkbookAgents(OfflineAgents):
    """Adapt existing fixture runners to any controller-selected retry focus."""
    revision_assessment = staticmethod(forbidden)

    def tutor(self, context):
        self.calls['tutor'] += 1
        lesson = json.loads((ROOT / 'lessons/cell-membranes.json').read_text())
        handoff = json.loads((ROOT / 'schemas/v1/examples/tutor_to_assessment.example.json').read_text())
        handoff['handoff_id'] = context['handoffId']
        handoff['context'].update(learner_session_id=context['sessionId'], round=context['round'])
        payload = handoff['payload']
        payload['explanation_id'] = context['explanationId']
        selected = lesson['concepts']
        if context['round'] == 'retry':
            focus = {w['concept_id'] for w in context['returnHandoff']['payload']['weak_concepts']}
            selected = [c for c in selected if c['concept_id'] in focus]
            payload['focus_concept_ids'] = [c['concept_id'] for c in selected]
        payload['concepts'] = [dict(concept_id=c['concept_id'], name=c['name'],
            taught_summary=lesson['source_passages'][c['concept_id']]) for c in selected]
        payload['explanation_text'] = '\n\n'.join(lesson['source_passages'][c['concept_id']] for c in selected)
        handoff['uncertainty']['reasons'] = ['Offline fixture assembled from approved passages; not a live Tutor response.']
        return dict(handoff=handoff, repairCount=0)

    def assessment(self, handoff, **kwargs):
        candidate = super().assessment(handoff, **kwargs)
        for key in candidate['answer_key']:
            key['rationale'] = key['rationale'].replace('Cholesterol prevents excessive stiffening',
                                                     'Cholesterol helps prevent excessive stiffening')
        return candidate


class LiveAgents:
    """Use existing adapters directly; no fixture fallback or new retry loop."""
    def __init__(self):
        self.tutor = run_tutor
        self.assessment = run_assessment
        self.reviewer = run_reviewer
        self.revision_assessment = run_assessment_revision
        self.feedback = run_feedback


def load_trusted_env(path):
    """Read only provider key/model settings from an explicit trusted file.

    No shell evaluation, expansion, execution, logging or browser transmission.
    Other environment settings (especially paid consent) cannot come from here.
    """
    values = {}
    for number, line in enumerate(Path(path).read_text().splitlines(), 1):
        line = line.strip()
        if line.startswith('export '):
            line = line[7:].strip()
        name, sep, value = line.partition('=')
        if not sep or name.strip() not in ('ANTHROPIC_API_KEY','ANTHROPIC_MODEL'):
            continue
        try:
            pieces = shlex.split(value, comments=True)
            if len(pieces) != 1:
                raise ValueError()
        except ValueError:
            raise ValueError(f'Invalid provider setting on line {number}; check the trusted file.') from None
        values[name.strip()] = pieces[0]
    for name, value in values.items():
        if not os.environ.get(name, "").strip():
            os.environ[name] = value


def connect(path):
    conn = sqlite3.connect(path, timeout=5)
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def prepare_database(path, learner_id=LEARNER):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.is_symlink():
        raise ValueError('Use a dedicated workbook database, not a symlink.')
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        with closing(connect(path)) as conn:
            if not conn.execute('SELECT 1 FROM learners WHERE learner_id=?', (learner_id,)).fetchone():
                raise ValueError('This is not an initialized workbook database; choose a new path.')
            if conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise ValueError('Workbook database integrity check failed.')
        with closing(connect(path)) as conn:
            with tempfile.TemporaryDirectory() as directory:
                for source in (ROOT / 'lessons').glob('*.json'):
                    lesson = json.loads(source.read_text())
                    if lesson.get('approved') is True and not conn.execute('SELECT 1 FROM lessons WHERE lesson_id=? AND version=?', (lesson['lesson_id'], lesson['version'])).fetchone():
                        shutil.copyfile(source, Path(directory) / source.name)
                load_lessons(conn, directory)
        return
    os.close(fd)
    try:
        with closing(connect(path)) as conn:
            conn.executescript((ROOT / 'db/schema.sql').read_text())
            load_lessons(conn)
            with conn:
                conn.execute('INSERT INTO learners VALUES (?)', (learner_id,))
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def _snapshot(conn, session_id, learner_id, mode):
    status = get_session_status(conn, learner_id, session_id)
    lesson = conn.execute('SELECT l.lesson_id,l.version,l.topic,l.source_url,l.license,l.attribution '
        'FROM study_sessions s JOIN lessons l ON l.lesson_id=s.lesson_id AND l.version=s.lesson_version '
        'WHERE s.session_id=? AND s.learner_id=?', (session_id, learner_id)).fetchone()
    result = dict(mode=mode, **status, lesson=dict(zip(('lesson_id','version','title','url','license','attribution'),lesson)),
                  explanation=None, quiz=None, attempts=[], summary=None)
    quizrow = conn.execute('SELECT quiz_id,explanation_id,round FROM quizzes WHERE session_id=? ORDER BY rowid DESC LIMIT 1',
                           (session_id,)).fetchone()
    if quizrow:
        explanation = conn.execute('SELECT text FROM explanations WHERE explanation_id=? AND session_id=?',
                                   (quizrow[1],session_id)).fetchone()
        concepts = [dict(concept_id=r[0], name=r[1]) for r in conn.execute(
            'SELECT c.concept_id,c.name FROM explanation_concepts e '
            'JOIN concepts c ON c.concept_id=e.concept_id WHERE e.explanation_id=? ORDER BY c.rowid', (quizrow[1],))]
        result['explanation'] = dict(text=explanation[0], round=quizrow[2], concepts=concepts)
        if status['quiz_available']:
            result['quiz'] = get_current_quiz(conn, learner_id, session_id)['quiz']
            result['quiz']['round'] = quizrow[2]
    # The latest saved explanation may be a retry being prepared while an old
    # submitted quiz still exists. Reading never waits for quiz publication.
    early = conn.execute('SELECT explanation_id,text,round FROM explanations WHERE session_id=? ORDER BY rowid DESC LIMIT 1', (session_id,)).fetchone()
    if early:
        concepts = [dict(concept_id=r[0],name=r[1]) for r in conn.execute(
            'SELECT c.concept_id,c.name FROM explanation_concepts e JOIN concepts c ON c.concept_id=e.concept_id WHERE e.explanation_id=? ORDER BY c.rowid', (early[0],))]
        result['explanation'] = dict(text=early[1],round=early[2],concepts=concepts)
    operation = conn.execute('SELECT stage,explanation_id,round FROM quiz_quality_operations WHERE session_id=? ORDER BY rowid DESC LIMIT 1', (session_id,)).fetchone()
    stage = operation[0] if operation else 'teaching'
    if status['status']=='reteaching' and (not operation or operation[2]!='retry' or early and operation[1]!=early[0]):stage='reteaching'
    if status['status']=='grading':stage='feedback'
    if status['status']=='completed':stage='completed'
    if status['status']=='pending_save':stage='pending_save'
    if status['status']=='failed':stage='failed'
    messages = {'teaching':'Tutor is preparing your explanation.', 'generating':'Assessment is creating your quiz.', 'reviewing':'Reviewer is checking your quiz.', 'revising':'Assessment is improving the flagged questions.', 'reviewing_revision':'Reviewer is checking the revised quiz.', 'approved':'Your quiz is ready.', 'feedback':'Assessment is preparing feedback on your saved answers.', 'reteaching':'Tutor is preparing notes on missed concepts.', 'completed':'Your summary is saved.', 'pending_save':'Your final summary is waiting to be saved.', 'failed':'Quiz preparation stopped. Your saved reading is preserved.'}
    result['preparation'] = dict(stage=stage,message=messages.get(stage,'Preparing your quiz.'))
    reason = conn.execute('SELECT failure_reason FROM study_sessions WHERE session_id=? AND learner_id=?',(session_id,learner_id)).fetchone()[0]
    failures = {
        'generation_output_truncated':'The AI quiz response was cut short. No incomplete quiz was published.',
        'content_revision_exhausted':'The quiz still needed corrections after its allowed revision, so it was not published.',
        'generation_request_timeout':'Quiz generation timed out before a complete response was saved.',
        'session_start_expired':'This session start expired before preparation completed.',
        'session_start_deadline':'Preparation stopped before Tutor finished. Start a new session when ready.',
        'quality_deadline':'Quiz preparation exceeded its deadline and stopped. Your explanation is still available; no unfinished quiz was published.',
        'review_invalid_report':'The quiz review returned an invalid response. The quiz was not published; your explanation is still available.',
        'review_uncertainty':'The review could not confirm that the quiz was suitable. The quiz was not published; your explanation is still available.'}
    if status['status']=='failed':
        result['failure'] = dict(code=reason if reason in failures else 'session_failed',message=failures.get(reason,'This session stopped before it could finish. Your saved progress is preserved.'))
        result['message']=result['failure']['message']
    for row in conn.execute('SELECT a.attempt_id,a.quiz_id,q.round,r.score,r.passed,r.feedback '
            'FROM attempts a JOIN quizzes q ON q.quiz_id=a.quiz_id JOIN results r ON r.attempt_id=a.attempt_id '
            'WHERE q.session_id=? AND a.learner_id=? ORDER BY a.rowid', (session_id,learner_id)):
        # Keys are only projected for an already submitted, owned attempt.
        questions = {q['question_id']:q for q in get_client_quiz(conn,row[1])['questions']}
        items=[]
        for r in conn.execute('SELECT q.question_id,q.position,q.concept_id,p.selected_option,p.is_correct,k.correct_option,k.rationale '
                'FROM questions q JOIN responses p ON p.question_id=q.question_id AND p.attempt_id=? '
                'JOIN answer_keys k ON k.question_id=q.question_id WHERE q.quiz_id=? ORDER BY q.position', (row[0],row[1])):
            items.append(dict(**questions[r[0]],concept_id=r[2],selected=r[3],is_correct=bool(r[4]),correct_option=r[5],rationale=r[6]))
        result['attempts'].append(dict(attempt_id=row[0],quiz_id=row[1],round=row[2],score=row[3],passed=bool(row[4]),
            items=items,feedback=json.loads(row[5]) if row[5] else None))
    if status['summary_available']:
        result['summary']=get_saved_summary(conn,learner_id,session_id)['summary']
    return result


def snapshot(conn, session_id, learner_id=LEARNER, mode=MODE):
    # Keep status, quiz, explanation and attempts in one read snapshot while a
    # separate request may be running a long provider operation.
    conn.execute('SAVEPOINT workbook_snapshot')
    try:
        return _snapshot(conn, session_id, learner_id, mode)
    finally:
        conn.execute('RELEASE workbook_snapshot')


class WorkbookServer(ThreadingHTTPServer):
    def __init__(self, database, port=8778, *, agent_mode='offline', allow_paid_calls=False):
        if agent_mode not in ('offline','live'):
            raise ValueError('Choose offline or live adapters.')
        self.database=Path(database)
        self.agent_mode=agent_mode
        self.mode=LIVE_MODE if agent_mode=='live' else MODE
        self.learner_id=LIVE_LEARNER if agent_mode=='live' else LEARNER
        self.allow_paid_calls=allow_paid_calls is True
        prepare_database(self.database,self.learner_id)
        # Reconcile abandoned calls before accepting a reconnect after a restart.
        # Maintenance only expires records; it never dispatches or retries AI.
        run_maintenance_cycle(self.database)
        self.next_maintenance=time.monotonic()+5
        self.token=secrets.token_urlsafe(32)
        self.agents=LiveAgents() if agent_mode=='live' else WorkbookAgents(lambda line:None)
        super().__init__(('127.0.0.1',port),WorkbookHandler)

    def service_actions(self):
        if time.monotonic()<self.next_maintenance:return
        self.next_maintenance=time.monotonic()+5
        try:
            run_maintenance_cycle(self.database)
        except sqlite3.OperationalError as error:
            if getattr(error,'sqlite_errorcode',0)&255 not in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED):raise
            # A subsequent cycle retries the idempotent bounded sweep.
            print('Workbook maintenance deferred: database busy.',file=sys.stderr,flush=True)

    def live_ready(self):
        return (self.agent_mode=='live' and self.allow_paid_calls
                and os.environ.get('STUDY_BUDDY_ALLOW_PAID_CLAUDE')=='true'
                and bool(os.environ.get('ANTHROPIC_API_KEY','').strip()))


class WorkbookHandler(BaseHTTPRequestHandler):
    def log_message(self, *_):
        pass  # No answer payloads, tokens or learner/session IDs in HTTP logs.

    def reply(self, code, payload, kind='application/json; charset=utf-8'):
        body=payload if isinstance(payload,bytes) else json.dumps(payload).encode()
        self.send_response(code)
        for key,value in {'Content-Type':kind,'Content-Length':str(len(body)),'Cache-Control':'no-store',
                'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer',
                'Content-Security-Policy':"default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"}.items():
            self.send_header(key,value)
        self.end_headers()
        self.wfile.write(body)

    def boundary(self, token=False):
        origin=f'http://127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != origin[7:]:
            self.reply(403,dict(error='Use the printed loopback URL.'));return False
        if self.headers.get('Origin') not in (None,origin) or self.headers.get('Sec-Fetch-Site')=='cross-site':
            self.reply(403,dict(error='Use this local workbook page.'));return False
        if token and not secrets.compare_digest(self.headers.get('X-Workbook-Token',''),self.server.token):
            self.reply(403,dict(error='Reload the workbook to reconnect.'));return False
        return True

    def do_GET(self):
        if not self.boundary():return
        url=urlsplit(self.path)
        assets={'/':('index.html','text/html'),'/student/':('index.html','text/html'),
                '/student/index.html':('index.html','text/html'),'/student/student.js':('student.js','text/javascript'),
                '/student/student.css':('student.css','text/css'),'/student/membrane.svg':('membrane.svg','image/svg+xml'),
                '/student/ux-audit.html':('ux-audit.html','text/html')}
        if url.path=='/':
            self.send_response(302);self.send_header('Location','/student/');self.end_headers();return
        if url.path in assets:
            name,kind=assets[url.path];return self.reply(200,(ROOT/'demo/student'/name).read_bytes(),kind+'; charset=utf-8')
        if url.path=='/api/config':
            return self.reply(200,dict(api_version=2,capabilities=['preparation_progress','source_reading','safe_failures'],token=self.server.token,mode=self.server.mode,paid_calls_enabled=self.server.live_ready(),
                message=('Real Tutor, Assessment and Reviewer adapters. Paid calls run only after your workbook actions.'
                         if self.server.agent_mode=='live' else
                         'Connected to the real controller. Offline fixture agents; synthetic review, not live quality approval.')))
        if not url.path.startswith('/api/'):return self.reply(404,dict(error='Not found.'))
        if not self.boundary(token=True):return
        try:
            with closing(connect(self.server.database)) as conn:
                if url.path=='/api/lessons':
                    if url.query:raise ValueError('Unexpected query.')
                    lessons=[dict(lesson_id=r[0],version=r[1],title=r[2],source_text=r[3],source_url=r[4],attribution=r[5],license=r[6],adaptation_notice=r[7]) for r in conn.execute(
                        'SELECT lesson_id,version,topic,source_text,source_url,attribution,license,adaptation_notice FROM lessons WHERE approved=1 '+
                        ("AND lesson_id='lesson_cell_membranes' AND version=2 " if self.server.agent_mode=='offline' else '')+'ORDER BY topic')]
                    return self.reply(200,dict(lessons=lessons))
                if url.path=='/api/start-progress':
                    query=parse_qs(url.query)
                    if set(query)!={'request_id'} or len(query['request_id'])!=1:raise ValueError('Request ID required.')
                    row=conn.execute('SELECT session_id FROM session_start_requests WHERE learner_id=? AND request_id=?', (self.server.learner_id,query['request_id'][0])).fetchone()
                    if not row:return self.reply(200,dict(pending=True))
                    return self.reply(200,snapshot(conn,row[0],self.server.learner_id,self.server.mode))
                if url.path=='/api/session':
                    query=parse_qs(url.query)
                    if set(query)!={'session_id'} or len(query['session_id'])!=1:raise ValueError('Session ID required.')
                    return self.reply(200,snapshot(conn,query['session_id'][0],self.server.learner_id,self.server.mode))
                return self.reply(404,dict(error='Not found.'))
        except ValueError:
            return self.reply(404,dict(error='The requested lesson or session is unavailable.'))
        except Exception:
            return self.reply(500,dict(error='Could not read saved progress. No new session was started.'))

    def do_POST(self):
        if not self.boundary(token=True):return
        try:
            if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('JSON required.')
            length=int(self.headers.get('Content-Length','0'))
            if not 0<length<=16384:raise ValueError('Invalid size.')
            self.connection.settimeout(10)
            payload=json.loads(self.rfile.read(length))
            if not isinstance(payload,dict):raise ValueError('Object required.')
            with closing(connect(self.server.database)) as conn:
                agents=self.server.agents
                if self.path=='/api/start':
                    if set(payload)!={'request_id','lesson_id','lesson_version'}:raise ValueError('Invalid start.')
                    if not isinstance(payload['lesson_version'],int) or isinstance(payload['lesson_version'],bool):raise ValueError('Version required.')
                    # Fixture content is limited to this approved local lesson.
                    if self.server.agent_mode=='offline' and (payload['lesson_id'],payload['lesson_version'])!=('lesson_cell_membranes',2):raise ValueError('Lesson unavailable.')
                    if not conn.execute('SELECT 1 FROM lessons WHERE lesson_id=? AND version=? AND approved=1', (payload['lesson_id'],payload['lesson_version'])).fetchone():raise ValueError('Lesson unavailable.')
                    if self.server.agent_mode=='live' and not self.server.live_ready():
                        return self.reply(409,dict(error='Live agents are connected but paid calls are disabled or the server key is missing. No session was started.'))
                    result=start_quiz_session(conn,self.server.learner_id,payload['lesson_id'],request_id=payload['request_id'],
                        lesson_version=payload['lesson_version'],tutor=agents.tutor,assessment=agents.assessment,
                        reviewer=agents.reviewer,revision_assessment=agents.revision_assessment)
                    return self.reply(200,snapshot(conn,result['session_id'],self.server.learner_id,self.server.mode))
                if self.path=='/api/submit':
                    if set(payload)!={'session_id','quiz_id','answers'} or not isinstance(payload['answers'],dict):raise ValueError('Invalid answers.')
                    snap=snapshot(conn,payload['session_id'],self.server.learner_id,self.server.mode)
                    owned=conn.execute('SELECT 1 FROM quizzes WHERE quiz_id=? AND session_id=?',
                                       (payload['quiz_id'],payload['session_id'])).fetchone()
                    if not owned:raise ValueError('Quiz unavailable.')
                    if self.server.agent_mode=='live' and not self.server.live_ready():
                        return self.reply(409,dict(error='Paid feedback calls are disabled. Your draft has not been submitted.'))
                    result=submit_answers(conn,self.server.learner_id,payload['quiz_id'],payload['answers'])
                    if result['status']=='grading':
                        explain_results(conn,self.server.learner_id,result['attempt_id'],assessment=agents.feedback)
                    return self.reply(200,snapshot(conn,payload['session_id'],self.server.learner_id,self.server.mode))
                if self.path=='/api/continue':
                    if set(payload)!={'session_id'}:raise ValueError('Session required.')
                    snap=snapshot(conn,payload['session_id'],self.server.learner_id,self.server.mode)
                    if snap['status'] in ('grading','reteaching'):
                        if self.server.agent_mode=='live' and not self.server.live_ready():
                            return self.reply(409,dict(error='Paid retry calls are disabled. Saved progress is unchanged.'))
                        attempt=snap['attempts'][-1]
                        if not attempt['feedback']:raise ValueError('Feedback is not ready.')
                        route=decide_next_step(conn,self.server.learner_id,attempt['attempt_id'])
                        if route['next_step']=='reteach':
                            retaught=dispatch_reteaching(conn,self.server.learner_id,attempt['attempt_id'],tutor=agents.tutor)
                            if retaught.get('next_step')=='assessment':
                                dispatch_retry_quiz(conn,self.server.learner_id,attempt['attempt_id'],assessment=agents.assessment,
                                    reviewer=agents.reviewer,revision_assessment=agents.revision_assessment)
                        else:
                            finish_session(conn,self.server.learner_id,payload['session_id'])
                    elif snap['status']=='pending_save':
                        finish_session(conn,self.server.learner_id,payload['session_id'])
                    elif snap['status'] not in ('awaiting_answers','completed'):
                        raise ValueError('Session cannot continue.')
                    return self.reply(200,snapshot(conn,payload['session_id'],self.server.learner_id,self.server.mode))
                return self.reply(404,dict(error='Not found.'))
        except StartRequestRejected as error:
            message = ('This start request belongs to a different lesson. Return to lesson selection and choose the chapter again.'
                if str(error)=='This request ID was already used with different lesson inputs.' else
                'The saved start request could not be resumed. Reconnect to saved progress before starting again.')
            return self.reply(409,dict(error=message))
        except SubmissionRejected as error:
            messages = {'This quiz was already submitted with different answers.':('answers_already_submitted','This quiz already has saved answers. Reconnect to view the original result; changed resubmissions are not allowed.')}
            code,message=messages.get(str(error),('submission_rejected','These answers could not be accepted for this quiz. Reconnect, then confirm all ten answers before submitting.'))
            return self.reply(409,dict(code=code,error=message,next_action='reconnect'))
        except (ValueError,TypeError,KeyError,IndexError) as error:
            messages = {'Lesson unavailable.':('lesson_unavailable','This lesson version is unavailable. Refresh the lesson catalog.'), 'Feedback is not ready.':('feedback_not_ready','Feedback is still preparing. Reconnect to read its progress.'), 'Session cannot continue.':('session_not_ready','This session cannot continue from its current state. Reconnect to read the saved next step.'), 'Quiz unavailable.':('quiz_unavailable','This quiz is unavailable in the selected session. Reconnect before answering.')}
            code,message=messages.get(str(error),('invalid_action','The requested action could not be accepted. Reconnect to the saved session before retrying.'))
            return self.reply(409,dict(code=code,error=message,next_action='reconnect'))
        except Exception:
            return self.reply(500,dict(error='The action did not finish. Saved progress may exist; reconnect before starting again.'))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8778)
    parser.add_argument('--database',type=Path)
    parser.add_argument('--agents',choices=('offline','live'),default='offline')
    parser.add_argument('--allow-paid-calls',action='store_true',help='Trusted server opt-in for user-initiated live sessions')
    parser.add_argument('--env-file',type=Path,help='Trusted provider key/model file; never served to the browser')
    args=parser.parse_args()
    if not 1<=args.port<=65535:parser.error('Choose a port between 1 and 65535.')
    if args.env_file:
        try:load_trusted_env(args.env_file)
        except (OSError,ValueError):parser.error('Could not read valid provider settings from the trusted environment file.')
    if args.allow_paid_calls and args.agents!='live':parser.error('Paid opt-in requires live adapters.')
    if args.allow_paid_calls:
        os.environ['STUDY_BUDDY_ALLOW_PAID_CLAUDE']='true'
    database=args.database or ROOT/'.student-local'/('workbook-live.sqlite' if args.agents=='live' else 'workbook.sqlite')
    if args.agents=='live':
        node=os.environ.get('STUDY_BUDDY_NODE') or shutil.which('node')
        if not node or not Path(node).is_file():parser.error('Node is required for real agents; set STUDY_BUDDY_NODE.')
        os.environ['STUDY_BUDDY_NODE']=node
    with WorkbookServer(database,args.port,agent_mode=args.agents,allow_paid_calls=args.allow_paid_calls) as server:
        print(f'Student workbook: http://127.0.0.1:{server.server_port}/student/',flush=True)
        print(('Live Tutor, Assessment and Reviewer; paid calls enabled for user actions.' if server.live_ready() else
               'Live adapters wired; paid calls disabled or key missing.' if args.agents=='live' else
               'Real controller + saved SQLite; offline fixture agents only. No paid calls.'),flush=True)
        try:server.serve_forever()
        except KeyboardInterrupt:pass

if __name__=='__main__':main()
