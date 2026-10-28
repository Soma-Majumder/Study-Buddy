"""Back up and apply an explicitly reviewed pending migration plan atomically."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from study_buddy.migration_backup import write_backup
from study_buddy.migration_runner import run_pending_migrations, MigrationExecutionFailed
from study_buddy.migrations import DEFAULT_MIGRATIONS, MigrationTrackingRejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='Existing database; pause application writes for maintenance')
    parser.add_argument('--apply', action='store_true', help='Explicitly authorize migration execution')
    parser.add_argument('--pending-sha256', required=True, help='Exact pending_sha256 from migration_status.py')
    parser.add_argument('--backup', type=Path, required=True, help='New backup file; never overwrites an existing path')
    parser.add_argument('--migrations-dir', type=Path, default=DEFAULT_MIGRATIONS, help='Trusted, reviewed migration directory')
    args = parser.parse_args()
    if not args.apply:
        parser.error('--apply is required; use migration_status.py for a read-only plan')
    try:
        with closing(sqlite3.connect(args.database.resolve().as_uri()+'?mode=rw', uri=True)) as conn:
            conn.execute('PRAGMA foreign_keys=ON')
            if args.backup.exists() or args.backup.is_symlink():
                raise FileExistsError(f'Backup path already exists: {args.backup}')
            # The backup is written under the write lock, after the plan is verified.
            result = run_pending_migrations(conn, expected_pending_sha256=args.pending_sha256, directory=args.migrations_dir,
                                            before_apply=lambda locked: write_backup(locked, args.backup))
            if args.backup.exists():
                result['backup'] = str(args.backup.resolve())
            print(json.dumps(result, indent=2))
    except (MigrationTrackingRejected, MigrationExecutionFailed, sqlite3.Error, OSError) as error:
        parser.exit(1, f'Migration execution failed: {error}\n')


if __name__ == '__main__':
    main()
