"""Run serial expiry sweeps until SIGINT/Ctrl+C or SIGTERM requests shutdown."""
import argparse
import json
from pathlib import Path
import signal
import sqlite3
import sys
from threading import Event

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from study_buddy.maintenance_worker import (run_maintenance_worker, WorkerConfigurationError,
    MaintenanceLockRetriesExhausted)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='Existing initialized application database')
    parser.add_argument('--limit', type=int, default=100)
    parser.add_argument('--interval', type=float, default=30, help='Seconds after each completed cycle (default: 30)')
    args = parser.parse_args()
    stop = Event()
    previous = {}
    def request_stop(signum, frame):
        stop.set()
    try:
        for signum in (signal.SIGINT, signal.SIGTERM):
            previous[signum] = signal.signal(signum, request_stop)
        result = run_maintenance_worker(args.database, limit=args.limit,
            interval_seconds=args.interval, stop_event=stop,
            on_cycle=lambda counts: print(json.dumps(dict(event='cycle_completed', **counts)), flush=True),
            on_retry=lambda info: print(json.dumps(dict(event='lock_retry', **info)), flush=True))
        print(json.dumps(dict(event='worker_stopped', **result)), flush=True)
    except WorkerConfigurationError as error:
        parser.exit(2, f'Invalid worker configuration: {error}\n')
    except MaintenanceLockRetriesExhausted:
        parser.exit(75, 'Maintenance worker stopped after temporary database lock retries; restart when contention clears.\n')
    except (sqlite3.Error, OSError, ValueError):
        parser.exit(1, 'Maintenance worker failed; check database availability and schema before restarting.\n')
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


if __name__ == '__main__':
    main()
