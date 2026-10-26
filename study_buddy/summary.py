"""Save an evidence-based final summary before marking a session complete."""
import json
import sqlite3

from study_buddy.answer_keys import get_answer_key
from study_buddy.grading import grade


class SummaryRejected(ValueError):
    pass


def finish_session(conn, learner_id, session_id):
    """Idempotent, learner-scoped completion. No AI call or new practice loop."""
    with conn:
        owned = conn.execute('UPDATE study_sessions SET status = status WHERE session_id = ? AND learner_id = ?',
                             (session_id, learner_id)).rowcount
        if not owned:
            raise SummaryRejected('Session is unavailable for this learner.')
        row = conn.execute(
            'SELECT s.status, s.lesson_id, s.lesson_version, s.learning_retry_count, '
            'l.topic, l.source_url, l.license, l.attribution FROM study_sessions s '
            'JOIN lessons l ON l.lesson_id = s.lesson_id AND l.version = s.lesson_version '
            'WHERE s.session_id = ? AND s.learner_id = ?', (session_id, learner_id),
        ).fetchone()
        if not row:
            raise SummaryRejected('Stored lesson version is unavailable.')
        status, lesson_id, version, retry_count, title, url, license_, credit = row
        previous = conn.execute('SELECT summary FROM session_summaries WHERE session_id = ?', (session_id,)).fetchone()
        if previous:
            if status != 'completed':
                raise SummaryRejected('Stored summary and session status do not agree.')
            return dict(session_id=session_id, status='completed', summary=json.loads(previous[0]), error=None)
        if status != 'pending_save':
            raise SummaryRejected('Session is not ready for its final summary.')
        attempts = conn.execute(
            'SELECT a.attempt_id, q.quiz_id, q.round, r.score, r.passed, a.submitted_at '
            'FROM attempts a JOIN quizzes q ON q.quiz_id = a.quiz_id '
            'JOIN results r ON r.attempt_id = a.attempt_id '
            'WHERE q.session_id = ? AND a.learner_id = ? ORDER BY a.rowid', (session_id, learner_id),
        ).fetchall()
        rounds = [a[2] for a in attempts]
        if rounds not in (['first'], ['first', 'retry']) or retry_count != len(attempts) - 1:
            raise SummaryRejected('A first attempt and any reserved retry must be fully saved.')
        if len(attempts) == 1 and attempts[0][3] < 8:
            raise SummaryRejected('The required learning retry has not been completed.')
        final_id = attempts[-1][0]
        route = conn.execute("SELECT context FROM session_routes WHERE attempt_id = ? AND session_id = ? AND route = 'summary' AND status = 'summary_pending'",
                             (final_id, session_id)).fetchone()
        if not route:
            raise SummaryRejected('The final attempt must have a pending summary route.')
        records = []
        for attempt_id, quiz_id, round_, score, passed, submitted_at in attempts:
            if not conn.execute("SELECT 1 FROM feedback_operations WHERE attempt_id = ? AND status = 'ready'", (attempt_id,)).fetchone():
                raise SummaryRejected('Validated feedback must be saved for every attempt.')
            keys = get_answer_key(conn, quiz_id)
            answers = dict(conn.execute('SELECT question_id, selected_option FROM responses WHERE attempt_id = ?', (attempt_id,)))
            if len(keys) != 10 or len(answers) != 10:
                raise SummaryRejected('A complete ten-question result is required.')
            try:
                graded = grade(keys, answers)
            except ValueError:
                raise SummaryRejected('Saved answers do not match their quiz.') from None
            weak = conn.execute(
                'SELECT w.concept_id, c.name, w.missed_count FROM weak_concepts w '
                'JOIN concepts c ON c.concept_id = w.concept_id '
                'WHERE w.source_attempt_id = ? AND w.learner_id = ? ORDER BY w.concept_id',
                (attempt_id, learner_id),
            ).fetchall()
            if (graded['score'] != score or graded['passed'] != bool(passed) or
                    {w[0]: w[2] for w in weak} != {w['concept_id']: w['missed_count'] for w in graded['weak_concepts']}):
                raise SummaryRejected('Summary evidence does not match verified grading.')
            records.append(dict(attempt_id=attempt_id, quiz_id=quiz_id, round=round_, score=score,
                                out_of=10, passed=bool(passed), submitted_at=submitted_at,
                                weak_concepts=[dict(concept_id=c, name=n, missed_count=m) for c, n, m in weak]))
        saved_at = conn.execute("SELECT strftime('%Y-%m-%dT%H:%M:%fZ', 'now')").fetchone()[0]
        summary = dict(schema_version='1.0', session_id=session_id,
                       lesson=dict(lesson_id=lesson_id, version=version, title=title,
                                   url=url, license=license_, attribution=credit),
                       attempts=records, learning_retry_count=retry_count,
                       concepts_to_revisit=records[-1]['weak_concepts'], saved_at=saved_at,
                       message='Session finished. These results describe this practice session, not long-term mastery.')
        conn.execute('SAVEPOINT save_final_summary')
        try:
            conn.execute('INSERT INTO session_summaries VALUES (?, ?, ?, ?)', (session_id, final_id, json.dumps(summary), saved_at))
            # The summary row exists before this status update, inside the same transaction.
            conn.execute("UPDATE study_sessions SET status = 'completed', failure_reason = NULL WHERE session_id = ?", (session_id,))
            conn.execute("UPDATE session_routes SET status = 'dispatched' WHERE attempt_id = ?", (final_id,))
        except sqlite3.Error:
            conn.execute('ROLLBACK TO save_final_summary')
            conn.execute('RELEASE save_final_summary')
            message = 'Could not save the final summary. Progress is saved; completion is pending.'
            conn.execute("UPDATE study_sessions SET status = 'pending_save', failure_reason = ? WHERE session_id = ?", (message, session_id))
            return dict(session_id=session_id, status='pending_save', summary=None, error=message)
        conn.execute('RELEASE save_final_summary')
    return dict(session_id=session_id, status='completed', summary=summary, error=None)
