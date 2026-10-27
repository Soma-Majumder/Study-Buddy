"""Preview full-schema baseline eligibility; no migrations or history are written."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from study_buddy.migration_baseline import plan_baseline
from study_buddy.migrations import MigrationTrackingRejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='Existing SQLite database, opened read-only')
    parser.add_argument('--through-version', type=int, required=True, help='Explicit reviewed baseline version (reviewed schemas: 8, 9 or 10)')
    args = parser.parse_args()
    try:
        with closing(sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro', uri=True)) as conn:
            print(json.dumps(plan_baseline(conn, through_version=args.through_version), indent=2))
    except (MigrationTrackingRejected, sqlite3.Error, OSError) as error:
        parser.exit(1, f'Baseline preview failed: {error}\n')


if __name__ == '__main__':
    main()
