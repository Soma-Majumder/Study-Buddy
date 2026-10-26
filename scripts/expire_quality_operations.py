"""Run one bounded crash-expiry sweep against an existing Study Buddy database."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from study_buddy.quality_expiry import sweep_expired_quality_operations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='Existing SQLite database with migrations 005–008 applied')
    parser.add_argument('--limit', type=int, default=100, help='Maximum operations checked (1–1000)')
    args = parser.parse_args()
    with closing(sqlite3.connect(args.database.resolve().as_uri() + '?mode=rw', uri=True)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        print(json.dumps(sweep_expired_quality_operations(conn, limit=args.limit)))


if __name__ == '__main__':
    main()
