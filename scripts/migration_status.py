"""Report recorded/pending migration files without changing the database."""
import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from study_buddy.migrations import plan_migrations, MigrationTrackingRejected


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='Existing SQLite database (opened read-only)')
    args = parser.parse_args()
    try:
        with closing(sqlite3.connect(args.database.resolve().as_uri() + '?mode=ro', uri=True)) as conn:
            print(json.dumps(plan_migrations(conn), indent=2))
    except (MigrationTrackingRejected, sqlite3.Error, OSError) as error:
        parser.exit(1, f'Migration status failed: {error}\n')


if __name__ == '__main__':
    main()
