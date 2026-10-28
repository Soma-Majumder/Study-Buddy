"""Back up and explicitly record a verified full-schema baseline atomically."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from study_buddy.migration_backup import write_backup
from study_buddy.migration_baseline import record_verified_baseline
from study_buddy.migrations import MigrationTrackingRejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='Existing database; pause application writes for maintenance')
    parser.add_argument('--record-baseline', action='store_true', help='Explicitly authorize writing baseline history')
    parser.add_argument('--through-version', type=int, required=True)
    parser.add_argument('--schema-sha256', required=True, help='Exact fingerprint from baseline_status.py')
    parser.add_argument('--manifest-sha256', required=True, help='Exact file-manifest fingerprint from baseline_status.py')
    parser.add_argument('--recorded-by', required=True, help='Operator identity for the audit')
    parser.add_argument('--reason', required=True, help='Why this existing database is being adopted')
    parser.add_argument('--backup', type=Path, required=True, help='New backup path; existing files are never overwritten')
    args = parser.parse_args()
    if not args.record_baseline:
        parser.error('--record-baseline is required; use baseline_status.py for a read-only preview')
    try:
        with closing(sqlite3.connect(args.database.resolve().as_uri()+'?mode=rw', uri=True)) as conn:
            if args.backup.exists() or args.backup.is_symlink():
                raise FileExistsError(f'Backup path already exists: {args.backup}')
            # The backup is written under the write lock, after the preview is re-verified.
            result = record_verified_baseline(conn, through_version=args.through_version,
                expected_schema_sha256=args.schema_sha256, expected_manifest_sha256=args.manifest_sha256,
                recorded_by=args.recorded_by, reason=args.reason,
                before_record=lambda locked: write_backup(locked, args.backup))
            result['backup'] = str(args.backup.resolve())
            print(json.dumps(result, indent=2))
    except (MigrationTrackingRejected, sqlite3.Error, OSError) as error:
        parser.exit(1, f'Baseline recording failed: {error}\n')


if __name__ == '__main__':
    main()
