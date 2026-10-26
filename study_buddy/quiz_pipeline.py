"""Learner routes use the bounded quality pipeline; no format-only publication."""
import copy
import json
from pathlib import Path
import sqlite3
import time

import jsonschema
from scripts.validate_quiz import check as check_quiz
from study_buddy.assessment import run_assessment, run_assessment_revision, AssessmentFailure
from study_buddy.reviewer import run_reviewer
from study_buddy.quality_state import (_operation, _active, QualityStateRejected, start_quality_operation,
                                      reserve_quality_call, complete_quality_call)
from study_buddy.quality_storage import store_candidate
from study_buddy.quality_dispatch import dispatch_quality_review, dispatch_quality_revision, _fail_quality_call, _fail_preparing_session
from study_buddy.quality_publication import publish_reviewed_quiz

SCHEMA=json.loads((Path(__file__).resolve().parent.parent/'schemas/v1/tutor_to_assessment.schema.json').read_text())


def _check_saved_handoff(conn, op, handoff):
    try:
        jsonschema.Draft202012Validator(SCHEMA).validate(handoff)
        p=handoff['payload']
        expected_policy='Exactly 10 multiple-choice questions, each with four options (a-d) and one concept_id from payload.concepts. Test only the explanation_text. Return a private answer key to the controller, never the learner or Tutor.'
        if handoff['expected_output'] != expected_policy:raise ValueError()
        if (handoff['handoff_id'], handoff['context']['learner_session_id'], handoff['context']['round'],
            p['explanation_id'],p['lesson_id'],p['lesson_version']) != (
            op['handoff_id'],op['session_id'],op['round'],op['explanation_id'],op['lesson_id'],op['lesson_version']):
            raise ValueError()
        text=conn.execute('SELECT text FROM explanations WHERE explanation_id=?',(op['explanation_id'],)).fetchone()
        ids={r[0] for r in conn.execute('SELECT concept_id FROM explanation_concepts WHERE explanation_id=?',(op['explanation_id'],))}
        taught=[c['concept_id'] for c in p['concepts']]
        names=dict(conn.execute('SELECT concept_id,name FROM concepts WHERE lesson_id=? AND lesson_version=?',(op['lesson_id'],op['lesson_version'])))
        source=conn.execute('SELECT source_url,license,attribution FROM lessons WHERE lesson_id=? AND version=? AND approved=1',(op['lesson_id'],op['lesson_version'])).fetchone()
        if (not text or p['explanation_text']!=text[0] or len(taught)!=len(ids) or set(taught)!=ids or
            any(c['name']!=names.get(c['concept_id']) or not c['taught_summary'].strip() for c in p['concepts']) or not source):
            raise ValueError()
        expected=[dict(lesson_id=op['lesson_id'],lesson_version=op['lesson_version'],url=source[0],license=source[1],attribution=source[2])]
        if handoff['sources']!=expected:raise ValueError()
        if op['round']=='retry':
            focus=p.get('focus_concept_ids',[])
            if len(focus)!=len(ids) or set(focus)!=ids:raise ValueError()
        elif 'focus_concept_ids' in p:raise ValueError()
    except (ValueError,KeyError,TypeError,jsonschema.ValidationError):
        raise QualityStateRejected('The original handoff must match the saved teaching and lesson.') from None


def start_quiz_pipeline(conn,learner_id,handoff,**runners):
    grant=start_quality_operation(conn,learner_id,handoff['context']['learner_session_id'],handoff['handoff_id'],
                                  handoff['payload']['explanation_id'],handoff['context']['round'])
    opid=grant['operation']['operation_id']
    with conn:
        op=_operation(conn,learner_id,opid)
        _check_saved_handoff(conn,op,handoff)
        previous=conn.execute('SELECT handoff_data FROM quiz_quality_inputs WHERE operation_id=?',(opid,)).fetchone()
        if previous and json.loads(previous[0])!=handoff:raise QualityStateRejected('The saved handoff is immutable.')
        if not previous:conn.execute('INSERT INTO quiz_quality_inputs VALUES (?,?)',(opid,json.dumps(handoff,allow_nan=False)))
    return continue_quality_quiz(conn,learner_id,opid,**runners)


def _learner_state(conn,learner_id,opid):
    with conn:
        op=_operation(conn,learner_id,opid)
        if op['stage']=='failed':_fail_preparing_session(conn,op,op['failure_code'])
        status=conn.execute('SELECT status FROM study_sessions WHERE session_id=?',(op['session_id'],)).fetchone()[0]
        return dict(operation_id=opid,session_id=op['session_id'],status=status,
                    operation_status='failed' if op['stage']=='failed' else 'running',
                    next_step=None,quiz=None,error=op['failure_code'])


def continue_quality_quiz(conn,learner_id,operation_id,*,assessment=run_assessment,reviewer=run_reviewer,
                          revision_assessment=run_assessment_revision):
    """Continue saved work without restarting an in-flight reservation.

    Inputs are server-owned operation IDs. This is the first-round save retry /
    crash continuation entry point; retry routes call it through their own claim.
    Injected runners must accept the timeout keyword arguments just like adapters.
    """
    for _ in range(6):
        with conn:op=_operation(conn,learner_id,operation_id)
        stage=op['stage']
        if stage=='approved':
            result=publish_reviewed_quiz(conn,learner_id,operation_id)
            result['operation_status']='ready' if result['quiz'] else ('pending_save' if result['error'] else 'ready')
            return result
        if stage=='failed':return _learner_state(conn,learner_id,operation_id)
        if stage in ('reviewing','reviewing_revision'):
            result=dispatch_quality_review(conn,learner_id,operation_id,reviewer=reviewer)
            if not result['dispatched'] and result['stage'] in ('reviewing','reviewing_revision'):
                return _learner_state(conn,learner_id,operation_id)
            continue
        if stage in ('needs_revision','revising','revision_invalid'):
            result=dispatch_quality_revision(conn,learner_id,operation_id,assessment=revision_assessment,reviewer=reviewer)
            if result['stage']=='revising':return _learner_state(conn,learner_id,operation_id)
            continue
        if stage not in ('generating','generation_invalid'):raise QualityStateRejected('Unknown quality pipeline stage.')
        kind='generation' if stage=='generating' else 'technical_repair'
        grant=reserve_quality_call(conn,learner_id,operation_id,kind)
        if not grant['reserved']:return _learner_state(conn,learner_id,operation_id)
        callid=grant['call_id']
        try:
            with conn:
                current=_operation(conn,learner_id,operation_id);_active(conn,current)
                row=conn.execute('SELECT handoff_data FROM quiz_quality_inputs WHERE operation_id=?',(operation_id,)).fetchone()
                if not row:raise QualityStateRejected('Missing saved original handoff.')
                handoff=json.loads(row[0]);_check_saved_handoff(conn,current,handoff)
        except (QualityStateRejected,ValueError,TypeError):
            _fail_quality_call(conn,learner_id,operation_id,callid,'generation_context_invalid')
            return _learner_state(conn,learner_id,operation_id)
        remaining=op['deadline_at']-time.time()
        if remaining<=1:
            _fail_quality_call(conn,learner_id,operation_id,callid,'quality_deadline')
            return _learner_state(conn,learner_id,operation_id)
        try:
            output=assessment(copy.deepcopy(handoff),repair_request=None if kind=='generation' else
                              'Return a complete structurally valid quiz with the original IDs, round and taught scope. This is the only shared technical repair.',
                              provider_timeout_ms=min(60_000,int(remaining*1000)-1000),process_timeout_seconds=min(70,remaining))
        except Exception as error:
            code=error.code if isinstance(error,AssessmentFailure) and isinstance(error.code,str) else 'adapter_error'
            _fail_quality_call(conn,learner_id,operation_id,callid,'generation_'+AssessmentFailure(code=code).code)
            return _learner_state(conn,learner_id,operation_id)
        try:valid=not check_quiz(output,handoff)
        except Exception:valid=False
        try:
            if valid:store_candidate(conn,learner_id,callid,output)
            else:complete_quality_call(conn,learner_id,callid,outcome='invalid')
        except sqlite3.Error:
            _fail_quality_call(conn,learner_id,operation_id,callid,'generation_save_failed')
            return _learner_state(conn,learner_id,operation_id)
        except (QualityStateRejected,ValueError,TypeError):
            _fail_quality_call(conn,learner_id,operation_id,callid,'generation_invalid_candidate')
            return _learner_state(conn,learner_id,operation_id)
    return _learner_state(conn,learner_id,operation_id)
