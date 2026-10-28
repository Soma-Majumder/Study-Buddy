"""Run the local HTTP API and the maintenance worker in one process.

Configuration comes from the environment (never from arguments, so secrets stay out of shell history):
  STUDY_BUDDY_DB                 absolute path of an existing database (see scripts/local_api_admin.py)
  STUDY_BUDDY_API_SECRET         shared secret, at least 32 characters
  STUDY_BUDDY_API_PORT           default 8787; the server only listens on 127.0.0.1
  STUDY_BUDDY_MAX_SESSIONS_PER_DAY  per learner, default 3
Live model calls also need ANTHROPIC_API_KEY and STUDY_BUDDY_ALLOW_PAID_CLAUDE=true.
"""
import os
from pathlib import Path
import signal
import sys
from threading import Event, Thread

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from study_buddy.http_api import Api, make_server
from study_buddy.maintenance_worker import run_maintenance_worker


def main():
    database = os.environ.get('STUDY_BUDDY_DB', '')
    secret = os.environ.get('STUDY_BUDDY_API_SECRET', '')
    if not database or not Path(database).is_absolute() or not Path(database).is_file():
        sys.exit('STUDY_BUDDY_DB must be the absolute path of an existing database.')
    try:
        port = int(os.environ.get('STUDY_BUDDY_API_PORT', '8787'))
        limit = int(os.environ.get('STUDY_BUDDY_MAX_SESSIONS_PER_DAY', '3'))
        api = Api(database, secret, max_sessions_per_day=limit)
    except ValueError as error:
        sys.exit(str(error))
    if os.environ.get('STUDY_BUDDY_ALLOW_PAID_CLAUDE', '').lower() != 'true':
        print('Note: STUDY_BUDDY_ALLOW_PAID_CLAUDE is not true, so live model calls are disabled.', file=sys.stderr)
    stop = Event()
    worker = Thread(target=run_maintenance_worker, args=(database,), kwargs=dict(stop_event=stop), daemon=True)
    worker.start()
    server = make_server(api, port=port)
    signal.signal(signal.SIGTERM, lambda *_: Thread(target=server.shutdown).start())
    print(f'Study Buddy API listening on 127.0.0.1:{port}')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        server.server_close()


if __name__ == '__main__':
    main()
