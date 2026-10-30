#!/bin/sh
set -eu
: "${ANTHROPIC_API_KEY:?Set ANTHROPIC_API_KEY in the hosting dashboard}"
: "${STUDY_BUDDY_PROXY_SECRET:?Set STUDY_BUDDY_PROXY_SECRET in the hosting dashboard}"
exec python -B scripts/student_workbook.py --host 0.0.0.0 --port "${PORT:-10000}" \
  --agents live --allow-paid-calls --public-proxy \
  --max-starts-per-day "${STUDY_BUDDY_MAX_STARTS_PER_DAY:-20}" \
  --database "${STUDY_BUDDY_DATABASE:-/var/data/workbook-live.sqlite}"
