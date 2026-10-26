"""Run one bounded crash-expiry sweep against an existing Study Buddy database."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from study_buddy.maintenance_worker import run_maintenance_cycle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('database', type=Path, help='Existing SQLite database with migrations 005–008 applied')
    parser.add_argument('--limit', type=int, default=100, help='Maximum operations checked (1–1000)')
    args = parser.parse_args()
    print(json.dumps(run_maintenance_cycle(args.database, limit=args.limit)))


if __name__ == '__main__':
    main()
