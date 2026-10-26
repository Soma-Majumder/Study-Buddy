"""Backend-only candidate/report staging; never use these reads in learner APIs."""
import hashlib
import json
import time

from study_buddy.quality_review import candidate_hash, validate_review
from study_buddy.quality_state import QualityStateRejected, _active, _operation, _snapshot, complete_quality_call
from scripts.validate_quiz import check as check_quiz


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)


def _fingerprint(value):
    return hashlib.sha256(_json(value).encode('utf-8')).hexdigest()


def _call(conn, learner_id, call_id):
    row = conn.execute('SELECT operation_id, kind, status, candidate_version, candidate_hash FROM quiz_quality_calls WHERE call_id = ?',
                       (call_id,)).fetchone()
    if not row:
        raise QualityStateRejected('Unknown quality call.')
    op = _operation(conn, learner_id, row[0])
    return op, row


def _teaching(conn, op):
    row = conn.execute('SELECT text FROM explanations WHERE explanation_id = ? AND session_id = ? AND round = ?',
                       (op['explanation_id'], op['session_id'], op['round'])).fetchone()
    if not row:
        raise QualityStateRejected('Saved teaching is unavailable.')
    ids = sorted(r[0] for r in conn.execute('SELECT concept_id FROM explanation_concepts WHERE explanation_id = ?',
                                          (op['explanation_id'],)))
    return row[0], ids


def _handoff(op, text, ids):
    payload = dict(lesson_id=op['lesson_id'], lesson_version=op['lesson_version'], explanation_id=op['explanation_id'],
                   explanation_text=text, concepts=[dict(concept_id=c) for c in ids])
    if op['round'] == 'retry': payload['focus_concept_ids'] = ids
    return dict(handoff_id=op['handoff_id'], context=dict(learner_session_id=op['session_id'], round=op['round']), payload=payload)


def _load_candidate(conn, op, version):
    row = conn.execute('SELECT candidate_hash, question_data, explanation_text, concept_ids FROM quiz_quality_candidates '
                       'WHERE operation_id = ? AND candidate_version = ?', (op['operation_id'], version)).fetchone()
    if not row:
        raise QualityStateRejected('Private candidate is unavailable.')
    digest, data, text, ids_json = row
    try:
        candidate = json.loads(data)
        if not isinstance(candidate, dict) or 'answer_key' in candidate:
            raise ValueError()
        candidate['answer_key'] = [dict(position=p, correct_option=o, rationale=r) for p, o, r in conn.execute(
            'SELECT position, correct_option, rationale FROM quiz_quality_candidate_keys '
            'WHERE operation_id = ? AND candidate_version = ? ORDER BY key_order', (op['operation_id'], version))]
        ids = json.loads(ids_json)
        if (candidate_hash(candidate) != digest or check_quiz(candidate, _handoff(op, text, ids)) or
                (text, ids) != _teaching(conn, op)):
            raise ValueError()
    except (ValueError, TypeError, KeyError):
        raise QualityStateRejected('Stored candidate or teaching snapshot is inconsistent.') from None
    return candidate, text, ids


def load_private_candidate(conn, learner_id, operation_id, candidate_version):
    """Owner-scoped backend read INCLUDING KEYS. Never return directly to a client."""
    with conn:
        op = _operation(conn, learner_id, operation_id)
        return _load_candidate(conn, op, candidate_version)[0]


def store_candidate(conn, learner_id, call_id, candidate):
    """Atomically save separate questions/keys and complete their reserved call."""
    with conn:
        op, call = _call(conn, learner_id, call_id)
        _, kind, status, version, _ = call
        if kind not in ('generation', 'revision', 'technical_repair'):
            raise QualityStateRejected('Only generator calls can save candidates.')
        digest = candidate_hash(candidate)
        existing = conn.execute('SELECT generating_call_id, candidate_hash FROM quiz_quality_candidates '
                                'WHERE operation_id = ? AND candidate_version = ?', (op['operation_id'], version)).fetchone()
        if existing:
            if existing != (call_id, digest) or _load_candidate(conn, op, version)[0] != candidate:
                raise QualityStateRejected('Candidate versions are immutable.')
            return _snapshot(conn, learner_id, op['operation_id'], stored=True, applied=False)
        if status != 'reserved':
            raise QualityStateRejected('Candidate call completed without staged payload; reconciliation is required.')
        text, ids = _teaching(conn, op)
        result = complete_quality_call(conn, learner_id, call_id, outcome='valid', candidate=candidate, manage_transaction=False)
        if not result['applied']:
            return dict(result, stored=False)
        question_data = {k: v for k, v in candidate.items() if k != 'answer_key'}
        now = time.time()
        conn.execute('INSERT INTO quiz_quality_candidates VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     (op['operation_id'], version, digest, call_id, _json(question_data), text, _json(ids), now))
        conn.executemany('INSERT INTO quiz_quality_candidate_keys VALUES (?, ?, ?, ?, ?, ?)', [
            (op['operation_id'], version, key['position'], order, key['correct_option'], key['rationale'])
            for order, key in enumerate(candidate['answer_key'])])
        return dict(result, stored=True)


def store_review_report(conn, learner_id, call_id, report):
    """Validate against the SAVED candidate, derive decision, and save atomically."""
    with conn:
        op, call = _call(conn, learner_id, call_id)
        _, kind, status, version, digest = call
        if kind not in ('review', 'review_revision'):
            raise QualityStateRejected('Only Reviewer calls can save reports.')
        existing = conn.execute('SELECT report_fingerprint FROM quiz_quality_reports WHERE review_call_id = ?', (call_id,)).fetchone()
        fingerprint = _fingerprint(report)
        if existing:
            if existing[0] != fingerprint:
                raise QualityStateRejected('Stored review reports are immutable.')
            _load_report(conn, op, call_id)
            return _snapshot(conn, learner_id, op['operation_id'], stored=True, applied=False)
        if status != 'reserved':
            raise QualityStateRejected('Reviewer call completed without report storage; reconciliation is required.')
        candidate, text, ids = _load_candidate(conn, op, version)
        context = dict(operation_id=op['operation_id'], candidate_version=version,
                       handoff=_handoff(op, text, ids), candidate=candidate)
        decision = validate_review(report, context)
        result = complete_quality_call(conn, learner_id, call_id, outcome=decision['decision'],
                                       reviewed_hash=decision['candidate_hash'], manage_transaction=False)
        if not result['applied']:
            return dict(result, stored=False)
        conn.execute('INSERT INTO quiz_quality_reports VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     (call_id, op['operation_id'], version, digest, _json(report), fingerprint, decision['decision'], time.time()))
        return dict(result, stored=True, decision=decision['decision'])


def _load_report(conn, op, call_id):
    row = conn.execute('SELECT candidate_version, candidate_hash, report_data, report_fingerprint, decision '
                       'FROM quiz_quality_reports WHERE review_call_id = ? AND operation_id = ?',
                       (call_id, op['operation_id'])).fetchone()
    if not row:
        raise QualityStateRejected('Private review report is unavailable.')
    version, digest, data, fingerprint, saved_decision = row
    candidate, text, ids = _load_candidate(conn, op, version)
    try:
        report = json.loads(data)
        decision = validate_review(report, dict(operation_id=op['operation_id'], candidate_version=version,
                                                handoff=_handoff(op, text, ids), candidate=candidate))
        if _fingerprint(report) != fingerprint or decision['candidate_hash'] != digest or decision['decision'] != saved_decision:
            raise ValueError()
    except (ValueError, TypeError):
        raise QualityStateRejected('Stored review report is inconsistent.') from None
    return report


def load_private_review_report(conn, learner_id, operation_id, review_call_id):
    """Backend-only audit read; report option judgments can reveal private answers."""
    with conn:
        op = _operation(conn, learner_id, operation_id)
        return _load_report(conn, op, review_call_id)


def build_private_reviewer_context(conn, learner_id, call_id):
    """Prepare private input for an already reserved review, without dispatching it.

    This read does not grant another dispatch: the caller must still hold the
    original reserved=True result. Never expose this payload to learner APIs.
    """
    with conn:
        op, call = _call(conn, learner_id, call_id)
        _, kind, status, version, digest = call
        expected = ('review', 'reviewing') if version == 0 else ('review_revision', 'reviewing_revision')
        if (kind, op['stage']) != expected or status != 'reserved':
            raise QualityStateRejected('An active reserved Reviewer call is required.')
        _active(conn, op)
        if time.time() >= op['deadline_at']:
            raise QualityStateRejected('Quality operation deadline has expired.')
        candidate, text, ids = _load_candidate(conn, op, version)
        if version != op['candidate_version'] or digest != op['candidate_hash'] or digest != candidate_hash(candidate):
            raise QualityStateRejected('Reviewer reservation does not match the saved candidate.')
        source = conn.execute('SELECT source_url, license, attribution FROM lessons '
                              'WHERE lesson_id = ? AND version = ? AND approved = 1',
                              (op['lesson_id'], op['lesson_version'])).fetchone()
        if not source:
            raise QualityStateRejected('Approved lesson source is unavailable.')
        header = {key: op[key] for key in ('operation_id', 'session_id', 'lesson_id', 'lesson_version',
                  'handoff_id', 'explanation_id', 'round', 'candidate_version', 'candidate_hash', 'hash_version', 'rubric_version')}
        return dict(controller_context=dict(schema_version='1.0', **header),
                    teaching=dict(explanation_text=text, concept_ids=ids,
                                  focus_concept_ids=ids if op['round'] == 'retry' else []),
                    sources=[dict(lesson_id=op['lesson_id'], lesson_version=op['lesson_version'],
                                  url=source[0], license=source[1], attribution=source[2])],
                    candidate=candidate, candidate_canonical_json=_json(candidate))


def build_private_revision_context(conn, learner_id, call_id):
    """Bind one reserved revision/technical correction to the saved v0 report."""
    with conn:
        op, call = _call(conn, learner_id, call_id)
        _, kind, status, version, _ = call
        if kind not in ('revision', 'technical_repair') or status != 'reserved' or version != 1 or op['stage'] != 'revising':
            raise QualityStateRejected('An active reserved content-revision call is required.')
        _active(conn, op)
        if time.time() >= op['deadline_at']:
            raise QualityStateRejected('Quality operation deadline has expired.')
        candidate, text, ids = _load_candidate(conn, op, 0)
        saved = conn.execute('SELECT review_call_id FROM quiz_quality_reports WHERE operation_id=? AND candidate_version=0',
                             (op['operation_id'],)).fetchone()
        if not saved:
            raise QualityStateRejected('A saved content-failure report is required.')
        report = _load_report(conn, op, saved[0])
        decision = validate_review(report, dict(operation_id=op['operation_id'], candidate_version=0,
                                               handoff=_handoff(op, text, ids), candidate=candidate))
        if decision['decision'] != 'needs_revision':
            raise QualityStateRejected('Only validated content failures authorize revision.')
        source = conn.execute('SELECT source_url, license, attribution FROM lessons WHERE lesson_id=? AND version=? AND approved=1',
                              (op['lesson_id'], op['lesson_version'])).fetchone()
        if not source:
            raise QualityStateRejected('Approved lesson source is unavailable.')
        header = {key: op[key] for key in ('operation_id', 'session_id', 'lesson_id', 'lesson_version',
                  'handoff_id', 'explanation_id', 'round', 'hash_version', 'rubric_version')}
        original = dict(controller_context=dict(schema_version='1.0', candidate_version=0,
                                                candidate_hash=candidate_hash(candidate), **header),
                        teaching=dict(explanation_text=text, concept_ids=ids, focus_concept_ids=ids if op['round']=='retry' else []),
                        sources=[dict(lesson_id=op['lesson_id'], lesson_version=op['lesson_version'],
                                      url=source[0], license=source[1], attribution=source[2])],
                        candidate=candidate, candidate_canonical_json=_json(candidate))
        return dict(original=original, review_report=report)
