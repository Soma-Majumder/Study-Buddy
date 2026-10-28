# Local HTTP API and tunnel

Runs the controller on one machine behind an authenticated HTTP wrapper (`study_buddy/http_api.py`). A tunnel gives it a public HTTPS address for a gateway to call. This is a pilot setup, not production hosting: the API is up only while the machine and tunnel are running, and SQLite lives on that machine's disk, so back the file up.

## Safety model

- The API listens on `127.0.0.1` only. Only the tunnel reaches it from outside.
- Every request needs `Authorization: Bearer <STUDY_BUDDY_API_SECRET>` and an `X-Learner-Id` for a registered learner. The secret belongs to the gateway; it must never reach a browser.
- Responses are learner projections. Quizzes have no answer key. Submitting answers returns only score, pass/fail and total; explanations arrive with the saved summary.
- Each learner may start `STUDY_BUDDY_MAX_SESSIONS_PER_DAY` sessions per UTC day (default 3), which bounds Anthropic spend. Also set a monthly spend limit in the Anthropic console.
- A public tunnel exposes this process to the internet. Keep the secret long and random, rotate it if it leaks, and stop the tunnel when not in use.

## Setup

```sh
python -B -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
pnpm --dir backend install --frozen-lockfile
.venv/bin/python -B scripts/local_api_admin.py init /absolute/path/study-buddy.db
.venv/bin/python -B scripts/local_api_admin.py add-learner /absolute/path/study-buddy.db   # one per student
```

## Run

Export these in the terminal that runs the server. Do not put them in command arguments or commit them.

```sh
export STUDY_BUDDY_DB=/absolute/path/study-buddy.db
export STUDY_BUDDY_API_SECRET="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"   # keep a copy for the gateway
export ANTHROPIC_API_KEY=...                      # from your .env; never print it
export STUDY_BUDDY_ALLOW_PAID_CLAUDE=true         # explicit opt-in to spend credits
.venv/bin/python -B scripts/run_api.py
```

## Endpoints

| Method and path | Purpose |
| --- | --- |
| `POST /sessions` `{lesson_id, request_id}` | Start in the background; returns 202 |
| `GET /session-requests/{request_id}` | Status of that start; includes `session_id` once reserved |
| `GET /sessions/{id}` | Status, `next_action`, `working` |
| `GET /sessions/{id}/quiz` | Current keyless quiz |
| `POST /quizzes/{quiz_id}/answers` `{answers}` | Grade; feedback, reteaching and retry quiz continue in the background |
| `GET /sessions/{id}/summary` | Saved summary |
| `POST /sessions/{id}/resume-summary` | Retry a failed final save |

## Tunnel

A tunnel such as Cloudflare Tunnel forwards a public HTTPS URL to `127.0.0.1:8787`. A free quick tunnel gets a new random URL each run, so the gateway's backend URL must be updated every time. A named tunnel keeps one URL but needs a Cloudflare account and a domain.
