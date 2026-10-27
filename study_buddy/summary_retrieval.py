"""Read completed summaries through a strict learner response projection."""
import json

from study_buddy.answer_keys import new_id


class SummaryRetrievalRejected(ValueError):
    pass


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Missing summary text')
    return value


def _weak(items):
    if not isinstance(items, list):
        raise ValueError('Invalid weak concepts')
    projected = []
    for item in items:
        count = item['missed_count']
        if type(count) is not int or not 1 <= count <= 10:
            raise ValueError('Invalid missed count')
        projected.append(dict(concept_id=_text(item['concept_id']), name=_text(item['name']), missed_count=count))
    if len({item['concept_id'] for item in projected}) != len(projected):
        raise ValueError('Duplicate weak concepts')
    return projected


def _read_summary(conn, learner_id, session_id):
    row = conn.execute(
        'SELECT s.status,s.lesson_id,s.lesson_version,s.learning_retry_count, '
        'f.final_attempt_id,f.summary,f.saved_at '
        'FROM study_sessions s LEFT JOIN session_summaries f ON f.session_id=s.session_id '
        'WHERE s.session_id=? AND s.learner_id=?', (session_id, learner_id)).fetchone()
    if row is None or row[0] != 'completed' or row[5] is None:
        raise ValueError('Unavailable summary')
    status, lesson_id, version, retry_count, final_id, raw, saved_at = row
    data = json.loads(raw)
    if (data['schema_version'] != '1.0' or data['session_id'] != session_id
            or type(data['learning_retry_count']) is not int or data['learning_retry_count'] != retry_count
            or data['saved_at'] != saved_at):
        raise ValueError('Summary identity mismatch')
    lesson = data['lesson']
    if lesson['lesson_id'] != lesson_id or type(lesson['version']) is not int or lesson['version'] != version:
        raise ValueError('Summary lesson mismatch')
    public_lesson = dict(lesson_id=lesson_id, version=version,
        **{key:_text(lesson[key]) for key in ('title','url','license','attribution')})
    if not public_lesson['url'].startswith(('https://','http://')):
        raise ValueError('Invalid source URL')
    attempts = data['attempts']
    if not isinstance(attempts,list) or len(attempts) != retry_count + 1:
        raise ValueError('Invalid attempt count')
    saved_attempts = conn.execute(
        'SELECT a.attempt_id,a.quiz_id,q.round,r.score,r.passed,a.submitted_at '
        'FROM attempts a JOIN quizzes q ON q.quiz_id=a.quiz_id JOIN results r ON r.attempt_id=a.attempt_id '
        'WHERE q.session_id=? AND a.learner_id=? ORDER BY a.rowid', (session_id, learner_id)).fetchall()
    if len(saved_attempts) != len(attempts) or saved_attempts[-1][0] != final_id:
        raise ValueError('Saved attempt mismatch')
    public_attempts=[]
    for index,(attempt,evidence) in enumerate(zip(attempts,saved_attempts)):
        if (type(attempt['score']) is not int or not 0 <= attempt['score'] <= 10
                or type(attempt['out_of']) is not int or attempt['out_of'] != 10
                or type(attempt['passed']) is not bool or attempt['passed'] != (attempt['score'] >= 8)
                or attempt['round'] != ('first' if index==0 else 'retry')
                or (attempt['attempt_id'],attempt['quiz_id'],attempt['round'],attempt['score'],
                    int(attempt['passed']),attempt['submitted_at']) != evidence):
            raise ValueError('Summary result mismatch')
        weak=_weak(attempt['weak_concepts'])
        saved_weak = conn.execute(
            'SELECT w.concept_id,c.name,w.missed_count FROM weak_concepts w '
            'JOIN concepts c ON c.concept_id=w.concept_id '
            'WHERE w.source_attempt_id=? AND w.learner_id=? AND w.lesson_id=? ORDER BY w.concept_id',
            (evidence[0],learner_id,lesson_id)).fetchall()
        if sorted((w['concept_id'],w['name'],w['missed_count']) for w in weak) != saved_weak:
            raise ValueError('Summary concepts mismatch')
        public_attempts.append(dict(attempt_id=evidence[0],quiz_id=evidence[1],round=evidence[2],score=evidence[3],
                                    out_of=10,passed=bool(evidence[4]),submitted_at=evidence[5],weak_concepts=weak))
    revisit=_weak(data['concepts_to_revisit'])
    if revisit != public_attempts[-1]['weak_concepts'] or (len(attempts)==1 and attempts[0]['score'] < 8):
        raise ValueError('Summary completion mismatch')
    return dict(session_id=session_id,status='completed',summary=dict(schema_version='1.0',session_id=session_id,
        lesson=public_lesson,attempts=public_attempts,learning_retry_count=retry_count,concepts_to_revisit=revisit,
        saved_at=_text(saved_at),message='Session finished. These results describe this practice session, not long-term mastery.'),error=None)


def get_saved_summary(conn, learner_id, session_id):
    """Return approved summary fields, never complete/save/regrade a session.

    The caller supplies authenticated identity. Read a consistent snapshot using
    a nested savepoint; preserve caller work and support read-only connections.
    Saved JSON is validated against scoped result/concept records, not keys or
    fresh model judgments. Unknown fields are omitted at every response level.
    """
    unavailable='Saved summary is unavailable for this learner and session.'
    if not all(isinstance(value,str) and value.strip() for value in (learner_id,session_id)):
        raise SummaryRetrievalRejected(unavailable)
    name=new_id('summary_read')
    conn.execute(f'SAVEPOINT {name}')
    try:
        try:
            return _read_summary(conn,learner_id,session_id)
        except (ValueError,TypeError,KeyError,IndexError):
            raise SummaryRetrievalRejected(unavailable) from None
    except BaseException:
        conn.execute(f'ROLLBACK TO {name}')
        raise
    finally:
        conn.execute(f'RELEASE {name}')
