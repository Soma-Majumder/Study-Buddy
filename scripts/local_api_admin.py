"""Create the local API database and register learners.

  python -B scripts/local_api_admin.py init /absolute/path/study-buddy.db
  python -B scripts/local_api_admin.py add-learner /absolute/path/study-buddy.db

init refuses an existing path, loads the approved lessons and creates owner-only files. It does not adopt a
migration baseline; do that separately (see backend/README.md) before using the migration tools.
add-learner prints a new learner ID. Give each student their own ID and keep the list private.
"""
import argparse
from contextlib import closing
import os
from pathlib import Path
import secrets
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from study_buddy.lessons import load_lessons
from study_buddy.migration_baseline import CURRENT_SCHEMA


def init(path):
    try:
        os.close(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
    except OSError as error:
        sys.exit(f'Could not create {path}: {error}')
    with closing(sqlite3.connect(path)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(CURRENT_SCHEMA.read_text())
        loaded = load_lessons(conn)
    print(f'Created {path} with {len(loaded)} approved lesson(s).')


def add_learner(path):
    with closing(sqlite3.connect(f'{Path(path).resolve().as_uri()}?mode=rw', uri=True)) as conn:
        conn.execute('PRAGMA foreign_keys=ON')
        learner_id = 'learner_' + secrets.token_hex(6)
        with conn:
            conn.execute('INSERT INTO learners VALUES (?)', (learner_id,))
    print(learner_id)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('command', choices=('init', 'add-learner'))
    parser.add_argument('database', type=Path, help='Absolute database path')
    args = parser.parse_args()
    if not args.database.is_absolute():
        parser.error('The database path must be absolute.')
    (init if args.command == 'init' else add_learner)(args.database)


if __name__ == '__main__':
    main()
