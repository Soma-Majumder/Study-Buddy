"""Create a new, empty application database from the complete current schema."""
import argparse
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from study_buddy.migration_baseline import CURRENT_SCHEMA
from study_buddy.migrations import discover_migrations


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='New database path; an existing path is never touched')
    args = parser.parse_args()
    version = discover_migrations()[-1]['version']
    created = False
    try:
        # Exclusive creation with owner-only permissions refuses existing paths, including symlinks.
        os.close(os.open(args.database, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
        created = True
        with closing(sqlite3.connect(args.database)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            conn.executescript(CURRENT_SCHEMA.read_text())
            if conn.execute('PRAGMA foreign_key_check').fetchone() or conn.execute('PRAGMA quick_check').fetchall() != [('ok',)]:
                raise sqlite3.DatabaseError('The new database failed its integrity checks.')
    except (sqlite3.Error, OSError) as error:
        if created:
            args.database.unlink(missing_ok=True)
        parser.exit(1, f'Database creation failed: {error}\n')
    path = args.database.resolve()
    print(f'Created {path} from db/schema.sql (migration {version:03d}). No lessons are loaded and no history is recorded.\n'
          f'Next: load approved lessons, then adopt the baseline with\n'
          f'  python -B scripts/baseline_status.py {path} --through-version {version}')


if __name__ == '__main__':
    main()
