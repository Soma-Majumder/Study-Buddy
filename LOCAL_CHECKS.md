# Offline backend checks

Run from the repository root after installing Python dependencies from `requirements-backend.lock` and Node dependencies with `pnpm --dir backend install --frozen-lockfile --ignore-scripts`:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --require-hashes -r requirements-backend.lock
export STUDY_BUDDY_NODE="$(command -v node)"
.venv/bin/python -B scripts/check_backend_local.py
```

The Python lock contains CPython 3.13/macOS arm64 wheel hashes; other platforms are not verified. Do not disable hashes to force installation. A network connection may be needed for dependency installation; the checks themselves do not call Claude.

The command checks selected source credential patterns and unexpected `.env` files, installed Python versions and dependency health, all Python tests, all backend JavaScript tests, and the existing pass/retry/blocked scenario report. Failures, empty test runs and skipped tests fail the gate. Test subprocesses have Claude/OpenAI credentials removed and paid opt-in disabled; adapters use fake transports. Some tests bind loopback.

Results are written to ignored `.local-checks/report.json` and text logs. The scan does not cover all Git history or every credential format. This is a manually run command, not an installed commit hook or GitHub CI.

The review branch retains existing interface files and tests. See [audit closeout](docs/evals/backend-audit-closeout-2026-10-28.html) for this snapshot’s recorded results and limits. Historical private traces are excluded, original outputs are not rewritten, and the corrected manual retry candidate is NOT Reviewer-approved or published.
