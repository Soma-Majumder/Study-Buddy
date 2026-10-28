"""Controller-owned Tutor repair authorization, persisted before the second call.

JSON-lines handshake keeps the database in Python. No provider call runs inside
an open database transaction, and a lost acknowledgement never refunds a repair.
"""
import json
import os
from pathlib import Path
import selectors
import subprocess
import time

ROOT = Path(__file__).resolve().parent.parent


def reserve_tutor_repair(conn, context):
    if conn.in_transaction:
        raise ValueError('Repair reservation requires no pending database work.')
    sid, hid = context['sessionId'], context['handoffId']
    with conn:
        # Acquire the write lock before checking state/deadline.
        conn.execute('UPDATE study_sessions SET status=status WHERE session_id=?', (sid,))
        expected = 'reteaching' if context.get('round') == 'retry' else 'teaching'
        row = conn.execute('SELECT learner_id,status,lesson_id FROM study_sessions WHERE session_id=?', (sid,)).fetchone()
        if not row or row[1:] != (expected, context['lessonId']):
            return False
        if expected == 'teaching' and conn.execute('SELECT 1 FROM session_start_requests WHERE session_id=?', (sid,)).fetchone():
            from study_buddy.session_start_requests import check_session_start_deadline_locked
            if not check_session_start_deadline_locked(conn, row[0], sid):
                return False
        if expected == 'reteaching':
            from study_buddy.operation_deadlines import check_operation_deadline_locked
            attempt = context['currentAttempt']['attemptId']
            if not check_operation_deadline_locked(conn, 'reteaching', row[0], attempt):
                return False
        claimed = conn.execute("UPDATE handoff_repairs SET repair_count=1 WHERE handoff_id=? AND session_id=? "
                               "AND direction='tutor_to_assessment' AND repair_count=0", (hid,sid)).rowcount
    return claimed == 1  # Commit is complete before the caller sends authorization.


def run_durable_tutor(conn, context, *, command=None, timeout_seconds=75):
    from study_buddy.controller import TutorFailure
    if conn.in_transaction:
        raise TutorFailure('repair budget validation')
    sid, hid = context['sessionId'], context['handoffId']
    def spent():
        row = conn.execute('SELECT repair_count FROM handoff_repairs WHERE session_id=? AND handoff_id=?', (sid,hid)).fetchone()
        return row[0] if row else 0
    if conn.execute('SELECT repair_count FROM handoff_repairs WHERE session_id=? AND handoff_id=?', (sid,hid)).fetchone() != (0,):
        raise TutorFailure('repair budget validation', spent())
    process = None
    try:
        process = subprocess.Popen(command or [os.environ.get('STUDY_BUDDY_NODE','node'), str(ROOT/'backend/tutor-command.mjs')],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=ROOT)
        process.stdin.write((json.dumps(dict(protocol='durable-tutor-v1',context=context))+'\n').encode())
        process.stdin.flush()
        deadline = time.monotonic()+timeout_seconds
        buffer = b''
        requested = False
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            while True:
                remaining = deadline-time.monotonic()
                if remaining <= 0 or not selector.select(remaining):
                    raise TimeoutError()
                chunk = os.read(process.stdout.fileno(),65536)
                if not chunk:
                    raise ValueError('Missing Tutor result.')
                buffer += chunk
                if len(buffer)>2_000_000:
                    raise ValueError('Tutor response exceeds bridge limit.')
                while b'\n' in buffer:
                    line,buffer = buffer.split(b'\n',1)
                    body = json.loads(line)
                    if not isinstance(body,dict):
                        raise ValueError('Invalid Tutor envelope.')
                    if body.get('event') == 'reserve_tutor_repair':
                        if requested:
                            raise ValueError('Repeated repair request.')
                        requested = True
                        allowed = reserve_tutor_repair(conn,context)
                        process.stdin.write((json.dumps(dict(repair_reserved=allowed))+'\n').encode())
                        process.stdin.flush()
                        continue
                    process.stdin.close()
                    process.wait(timeout=max(0.01,deadline-time.monotonic()))
                    if body.get('ok') is not True or process.returncode != 0:
                        error = body.get('error',{})
                        stage = error.get('stage') if isinstance(error,dict) else None
                        raise TutorFailure(stage if stage in ('input preparation','model call','output validation') else 'provider adapter',
                                           spent(), 'live_calls_disabled' if isinstance(error,dict) and error.get('code')=='live_calls_disabled' else None)
                    result = body['result']
                    if type(result.get('repairCount')) is not int or result['repairCount'] != spent():
                        raise ValueError('Tutor repair count does not match reservation.')
                    return result
    except TutorFailure:
        raise
    except Exception:
        raise TutorFailure('provider adapter',spent()) from None
    finally:
        if process is not None:
            if process.poll() is None:
                process.kill()
            process.wait()
            for stream in (process.stdin,process.stdout):
                if stream and not stream.closed:
                    stream.close()
