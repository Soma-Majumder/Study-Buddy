"""First routing step: approved lesson -> Tutor -> stored explanation or failure.

learner_id must come from trusted application context, not an agent. This does
not authenticate users or dispatch Assessment. Use a connection with schema.sql
and approved lessons already loaded by study_buddy.lessons.load_lessons.
"""
import json
import os
from pathlib import Path
import re
import subprocess

import jsonschema

from study_buddy.answer_keys import new_id

ROOT = Path(__file__).resolve().parent.parent
HANDOFF_SCHEMA = json.loads((ROOT / 'schemas/v1/tutor_to_assessment.schema.json').read_text())


class TutorFailure(Exception):
    def __init__(self, stage='model call', repair_count=0):
        super().__init__('Tutor could not produce a valid explanation.')
        self.stage = stage
        self.repair_count = repair_count


def run_tutor(context):
    """Invoke the existing JS harness. Credentials stay in the environment."""
    node = os.environ.get('STUDY_BUDDY_NODE', 'node')
    try:
        process = subprocess.run(
            [node, str(ROOT / 'backend/tutor-command.mjs')],
            input=json.dumps(context), text=True, capture_output=True,
            cwd=ROOT, timeout=75,
        )
        body = json.loads(process.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise TutorFailure('provider adapter') from None
    if process.returncode != 0 or not body.get('ok'):
        error = body.get('error', {})
        count = error.get('repairCount', 0)
        raise TutorFailure(error.get('stage', 'provider adapter'), count if count in (0, 1) else 0)
    return body['result']


def _check_handoff(handoff, context, lesson, concepts):
    jsonschema.Draft202012Validator(HANDOFF_SCHEMA).validate(handoff)
    p = handoff['payload']
    if (handoff['handoff_id'] != context['handoffId'] or
            handoff['context']['learner_session_id'] != context['sessionId'] or
            handoff['context']['round'] != 'first' or
            p['explanation_id'] != context['explanationId'] or
            p['lesson_id'] != context['lessonId'] or p['lesson_version'] != lesson[0]):
        raise ValueError('Handoff does not belong to the current session and lesson.')
    if handoff['sources'] != [{
            'lesson_id': context['lessonId'], 'lesson_version': lesson[0],
            'url': lesson[1], 'license': lesson[2], 'attribution': lesson[3]}]:
        raise ValueError('Handoff sources do not match the approved lesson.')
    seen = set()
    for c in p['concepts']:
        if (c['concept_id'] not in concepts or c['concept_id'] in seen or
                c['name'] != concepts[c['concept_id']] or not c['taught_summary'].strip()):
            raise ValueError('Invalid taught concepts.')
        seen.add(c['concept_id'])
    if not p['explanation_text'].strip() or 'focus_concept_ids' in p:
        raise ValueError('Invalid first-round explanation.')


def start_session(conn, learner_id, lesson_id, *, tutor=run_tutor):
    """Return {session_id, status, next_step, handoff, error}.

    The real Tutor harness owns generation validation and its one repair.
    Controller validation is a final boundary check; it never restarts the
    harness or grants another repair. Repair outcomes are saved on return;
    crash-safe repair reservation during the JS call remains future work.
    """
    if not isinstance(learner_id, str) or not re.fullmatch(r'learner_[a-z0-9]{12}', learner_id):
        raise ValueError('A controller-owned learner ID is required.')
    if not conn.execute('SELECT 1 FROM learners WHERE learner_id = ?', (learner_id,)).fetchone():
        raise ValueError('Learner is not registered.')
    lesson = conn.execute(
        'SELECT version, source_url, license, attribution FROM lessons '
        'WHERE lesson_id = ? AND approved = 1 ORDER BY version DESC LIMIT 1', (lesson_id,),
    ).fetchone()
    if not lesson:
        raise ValueError('An approved lesson is required.')
    concepts = dict(conn.execute(
        'SELECT concept_id, name FROM concepts WHERE lesson_id = ? AND lesson_version = ?',
        (lesson_id, lesson[0]),
    ))
    context = dict(lessonId=lesson_id, sessionId=new_id('session'),
                   handoffId=new_id('handoff'), explanationId=new_id('expl'), round='first')
    session_id = context['sessionId']
    with conn:
        conn.execute(
            "INSERT INTO study_sessions (session_id, learner_id, lesson_id, lesson_version, status, created_at) "
            "VALUES (?, ?, ?, ?, 'teaching', strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))",
            (session_id, learner_id, lesson_id, lesson[0]),
        )
        conn.execute(
            "INSERT INTO handoff_repairs VALUES (?, ?, 'tutor_to_assessment', 0)",
            (context['handoffId'], session_id),
        )
    repair_count = 0
    try:
        result = tutor(context)
        repair_count = result['repairCount']
        if type(repair_count) is not int or repair_count not in (0, 1):
            raise ValueError('Invalid repair count.')
        handoff = result['handoff']
        _check_handoff(handoff, context, lesson, concepts)
    except Exception as error:
        count = error.repair_count if isinstance(error, TutorFailure) else repair_count
        return _fail(conn, context, count if type(count) is int and count in (0, 1) else 0,
                     'Tutor did not return a valid explanation. No quiz was started.')

    try:
        with conn:
            conn.execute('INSERT INTO explanations VALUES (?, ?, ?, ?)',
                         (context['explanationId'], session_id, 'first', handoff['payload']['explanation_text']))
            conn.executemany('INSERT INTO explanation_concepts VALUES (?, ?)',
                             [(context['explanationId'], c['concept_id']) for c in handoff['payload']['concepts']])
            conn.execute('UPDATE handoff_repairs SET repair_count = ? WHERE handoff_id = ?',
                         (repair_count, context['handoffId']))
    except Exception:
        return _fail(conn, context, repair_count, 'Could not save the explanation. No quiz was started.')
    # A quiz does not exist yet: keep teaching status, never awaiting_answers or completed.
    return dict(session_id=session_id, status='teaching', next_step='assessment', handoff=handoff, error=None)


def _fail(conn, context, repair_count, message):
    # If recording failure itself fails, propagate the DB error instead of claiming a save.
    with conn:
        conn.execute("UPDATE study_sessions SET status = 'failed', failure_reason = ? WHERE session_id = ?",
                     (message, context['sessionId']))
        conn.execute('UPDATE handoff_repairs SET repair_count = ? WHERE handoff_id = ?',
                     (repair_count, context['handoffId']))
    return dict(session_id=context['sessionId'], status='failed', next_step=None, handoff=None, error=message)
