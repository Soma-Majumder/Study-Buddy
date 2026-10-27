# Study Buddy backend guide

Current operation as of October 26, 2026, based on merged `dev` commit `8c53b0d`. Use the [project README](../README.md) for the product overview and delivery-status table. The [Google PRD](https://docs.google.com/document/d/1MuA6lEJigDXQGIhVv3FKvgnLPPqIrBOd5FBY1T0qZXY/edit?tab=t.6wuguzynoy44) is the product source of truth; [the PDF](../docs/Study-Buddy-PRD.pdf) is its dated export.

## Implemented, under review and planned

**Implemented in dev:** approved lesson loading; Tutor teaching/reteaching; Assessment quiz generation and result explanations; Reviewer dispatch, bounded content revision and exact-candidate publication; deterministic grading; one learning retry; saved attempts/feedback/weak concepts and final summaries; learner-scoped ownership; duplicate-submission replay; paid-call opt-in; crash expiry; maintenance worker; migration tracking/baselining/execution. This is backend functionality, not a production deployment.

**Under review:** [PR #47](https://github.com/Soma-Majumder/Study-Buddy/pull/47) adds read-only `get_session_status`, `get_current_quiz` and `get_saved_summary` reconnect actions. Do not assume they exist on `dev` until merged.

**Local prototype, not merged:** `codex/lesson-picker` provides a terminal fixture demo. It does not run live agents or save progress. Keep it distinct from backend integration and the planned web interface.

**Planned / not yet verified:** authenticated web routes/UI, returning-learner experience, additional pilot lessons, P1 confidence/misconception/spaced-review features, production supervision/hosting and independent live Reviewer validation. P0/P1/P2 in the PRD indicate priority, not shipped state. No student-pilot target is claimed as achieved.

## Setup and offline checks

From the repository root, create/activate a Python environment, install `requirements-dev.txt`, and run `pnpm --dir backend install --frozen-lockfile`. Then:

```sh
python -B -m unittest discover -q
pnpm --dir backend test
```

Python uses SQLite and jsonschema; Node adapters use AJV and AJV formats. `node` must be available on PATH, or set `STUDY_BUDDY_NODE` to its absolute path for Python bridges. The tests use fixtures/stub transports without paid model calls. Concurrency and database-failure tests validate workflow mechanics, not scientific correctness or educational outcomes. Existing test-suite ResourceWarnings about unclosed legacy SQLite fixtures may still appear despite a passing run.

## Controller flow and ownership

`study_buddy.session_flow.run_study_session` sequences the existing controller steps. It accepts a trusted learner ID, lesson ID and `answer_quiz(client_quiz)` callback returning `{question_id: 'a'|'b'|'c'|'d'}`. The published learner quiz has no private answer key. Use initialized SQLite with foreign keys enabled, approved lessons loaded via `study_buddy.lessons.load_lessons`, and the current learner record already created. Caller-supplied identity is not an authentication implementation.

The flow is Tutor → Assessment candidate → controller contract checks → Reviewer → optional single content revision and second review → exact publication → student submission → deterministic grading → Assessment feedback → optional Tutor reteaching and one focused reviewed retry → saved summary → completed. After the learning retry, finish regardless of score. Tutor never receives the private key. Only controller code writes validated state.

Tutor owns teaching; Assessment owns quiz generation/revision and explanation of verified results; Reviewer supplies evidence-bound content findings. Ivaylo leads orchestration/persistence/recovery; Soma leads Assessment behavior, rubric and benchmarks. Changes to shared contracts need joint review.

## Contracts, review and budgets

Versioned contracts/examples live in `schemas/v1/`. Example IDs and hand-written fixture text are development data, not production-generated IDs or evidence of a live model run. Do not add planned P1 fields to strict v1 schemas without a versioned protocol change.

- Exactly 10 multiple-choice questions, options a–d, per first/retry quiz; pass threshold **8/10**.
- One student learning retry per session, separate from technical repairs and content revision.
- Tutor and Assessment share the outgoing handoff's one technical repair budget. Assessment result explanations/return handoffs follow their own validated operation rules. A malformed Reviewer response fails explicitly without its own repair loop.
- Each quiz-quality operation allows one initial generation, at most one available technical correction, first review, one content revision and final review: at most five provider reservations. A content revision never refreshes the handoff budget.
- Operation deadline: 180 seconds; provider calls are capped at 60 seconds and shortened to the remaining allowance. Reserved calls left by a crash are not automatically resent. Expiry reconciles state without new calls.
- Private candidate questions, keys and review reports are stored with exact version/hash bindings. Publication atomically saves the exact approved candidate and ready status; a stale approval cannot publish changed content.
- Current quality policy: six distinct understanding items is the generation goal. Insufficient understanding and repeated-target findings are warnings; quiz-level answer cues are conditional warnings when no individual question fails. Blocking question defects still prevent publication. See [the detailed policy](../docs/quiz-quality-gate-design.md) and `study_buddy/quality_review.py`; model judgments still need independent evaluation.

## Submission replay and final saving

`controller.submit_answers` serializes claims and saved-attempt reads. Identical complete resubmissions return the saved attempt/result and grading evidence without grading again. Changed answers are rejected. Simultaneous submissions create one attempt. Replay reports current session status and does not rewind routing or start a second feedback operation. This trusted controller return value contains private grading details and must not be exposed as an unfiltered public HTTP response.

`summary.finish_session(conn, trusted_learner_id, session_id)` verifies complete saved attempts, feedback, final route and grading evidence. Summary insertion, completion status and route dispatch are atomic. A failed summary save stays `pending_save`; progress is preserved. Call `finish_session` again with the same session ID to retry. Repeated successful calls return the same summary; no agents run here. A failed error-state write propagates rather than claiming success. `session_flow.resume_summary` (PR #45, merged) wraps this retry for callers holding only the session ID.

## Configuration and live evaluations

`.env` is ignored. Required live-call variables are `ANTHROPIC_API_KEY` and explicit `STUDY_BUDDY_ALLOW_PAID_CLAUDE` opt-in; use placeholders when documenting values. Never commit keys or print raw credentials/provider error bodies. An API key alone is insufficient: the shared Claude transport requires `STUDY_BUDDY_ALLOW_PAID_CLAUDE=true` or an explicit trusted adapter opt-in. Runtime environment configuration must be supplied to Python entry points; `.env` loading differs by entry point.

Live evaluation commands have their own explicit `--live` gates and can spend API credits. Do not include them in normal offline tests. For a deliberate end-to-end smoke run, inspect `backend/evals/session_flow_live.py` and [the evaluation guide](evals/README.md), supply `ANTHROPIC_API_KEY` to the process environment, and use an absolute report output path. This runner uses isolated SQLite and a simulated learner, not a deployed web app or student study. Its `--live` flag opts in for that process only. Reports/logs should keep safe codes, counts, timings and numeric usage; private raw response capture is not normal operational logging.

## Database initialization and migrations

`db/schema.sql` is the full current application schema. Numbered migrations 001–008 are incremental additions and do not bootstrap the original base tables. Initialize a new database with the full schema, enable foreign keys, load approved lessons, and explicitly adopt a verified baseline before using migration tracking. Existing partially upgraded or customized schemas require manual review; do not guess adoption from table names.

Read-only status commands, from the repository root:

```sh
python -B scripts/migration_status.py /absolute/path/to/study-buddy.db
python -B scripts/baseline_status.py /absolute/path/to/study-buddy.db --through-version 8
```

For a verified existing full schema, pause application writes, review the preview, then deliberately record the baseline. Replace placeholder fingerprints with the exact preview values:

```sh
python -B scripts/baseline_database.py /absolute/path/to/study-buddy.db \
  --record-baseline --through-version 8 \
  --schema-sha256 SCHEMA_SHA256_FROM_PREVIEW \
  --manifest-sha256 MANIFEST_SHA256_FROM_PREVIEW \
  --recorded-by "operator name" --reason "Adopt verified existing full schema" \
  --backup /absolute/path/to/new-baseline-backup.db
```

Baseline verifies schema objects/constraints and data integrity, records versions 001–008 plus an operator/reason audit, and commits all metadata together. It is adoption evidence, not proof that historical migration SQL ran on a particular date. Repeating adoption is rejected.

After adding/reviewing new migration SQL, obtain `pending_sha256` from `migration_status.py` and run:

```sh
python -B scripts/migrate_database.py /absolute/path/to/study-buddy.db \
  --apply --pending-sha256 PENDING_SHA256_FROM_STATUS \
  --backup /absolute/path/to/new-migration-backup.db
```

Commands open existing databases only and refuse backup overwrite. Backups use SQLite's backup API and owner-only permissions. Execution rechecks history/files under `BEGIN IMMEDIATE`, executes statements individually, records exact fingerprints and commits the batch together. Failures roll back SQL and history. Unsupported transaction controls, PRAGMAs, attached databases, temporary objects/triggers, extensions or metadata modifications are rejected. This is trusted migration tooling, not an arbitrary-SQL sandbox. Keep backups on failure; no application database was migrated as part of documenting these commands.

## Crash cleanup and maintenance worker

One-shot bounded cleanup:

```sh
python -B scripts/expire_quality_operations.py /absolute/path/to/study-buddy.db --limit 100
```

Repeating foreground worker:

```sh
python -B scripts/run_maintenance_worker.py /absolute/path/to/study-buddy.db --limit 100 --interval 30
```

Each cycle owns/closes a fresh connection, opens an existing initialized database and returns counts only. Cycles run serially with an interruptible wait. SIGINT/Ctrl+C or SIGTERM wakes waiting; an active batch finishes before exit. No new agent calls or learning retries are made.

The repeating worker retries only SQLite BUSY/LOCKED codes, including extended codes, with three waits of 1, 2 and 4 seconds. Success resets the per-cycle budget; errors without a matching SQLite code are not guessed from message text. Retry waits are interruptible. Lock waiting is up to five seconds per SQLite acquisition, not a hard total runtime guarantee. One-shot cleanup does not retry.

Exit codes: 0 clean shutdown; 1 runtime/database failure; 2 invalid operator configuration; 75 temporary-lock retries exhausted. Sweep `ValueError` failures are runtime errors, not mislabeled configuration. Output includes `cycle_completed`, `lock_retry`, and `worker_stopped` with counts or delays only. Production still needs a chosen host, supervisor/restart policy and monitoring. This repository installs no OS service or scheduler.

## Read-only session status

`controller.get_session_status(conn, trusted_learner_id, session_id)` returns an owned-session snapshot using one SELECT: `session_id`, actual saved `status`, display `stage`, `learning_retry_count`, `quiz_available`, active `quiz_id` (or null), `summary_available`, and a fixed safe `message` (or null). Ownership failures and unknown IDs use the same `SessionStatusRejected` message. The caller supplies authenticated identity; this helper does not implement authentication or a public HTTP endpoint.

Quiz availability requires `awaiting_answers`, the latest quiz's saved publication binding and no saved attempt. Completed summaries are advertised only when their row exists. Actual state is reported without inferring completion, invoking agents, retrying saves or sweeping deadlines. Raw failure reasons, questions, keys, candidate data, feedback and summary bodies are not read or returned. Availability is metadata: actual quiz retrieval must still pass controller publication/ownership checks. The query neither commits nor rolls back caller work and also works on a read-only SQLite connection. Preparing operations abandoned past their deadline remain in their saved state until the separate expiry process reconciles them.

## Read-only current quiz retrieval

`controller.get_current_quiz(conn, trusted_learner_id, session_id)` returns `{session_id, status, quiz, next_step}` for an owned session awaiting answers. The quiz contains only quiz/question IDs, positions, prompts and a–d choices. Ownership is checked before private evidence. The latest quiz must be unanswered and have a saved publication binding matching its operation, session, lesson/version, teaching/round and retry count. Existing approval/report/generator fingerprints and exact published questions/private keys are rechecked internally before projecting the learner response. Private audit records and answers are never returned.

Reads share a SQLite snapshot via a nested savepoint; this supports read-only connections, makes no application writes and preserves an existing caller transaction on success or rejection. It does not publish, dispatch agents, alter budgets or sweep expiry. An already published quiz remains retrievable after its preparation deadline. Wrong-owner, missing, stale, unreviewed or inconsistent requests raise a fixed `QuizRetrievalRejected` message; database infrastructure errors propagate. This is a controller callable, not a public HTTP/authentication implementation. The raw `answer_keys.get_client_quiz` remains an internal projection and must not be exposed without these checks.


## Read-only completed summary retrieval

`controller.get_saved_summary(conn, trusted_learner_id, session_id)` reads an owned completed session and its existing summary without calling `finish_session`, agents or grading. It validates the saved JSON identity, lesson/version, final attempt, scores, rounds and weak concepts against scoped persisted evidence, then explicitly projects approved summary/lesson/attempt/concept fields. Unexpected JSON fields are omitted and the learner-facing disclaimer is fixed; no raw arbitrary stored message, answer keys or private review data are returned.

A missing, malformed, inconsistent or non-completed summary raises the same `SummaryRetrievalRejected` message as an unavailable/wrong-owner session. Database errors propagate. All reads share a snapshot via a nested savepoint, support a read-only connection and preserve pending caller work. This is a controller callable, not authentication or a public HTTP endpoint. Summary retrieval and the persisted reconnect integration tests are included in PR #47 alongside status and current-quiz retrieval as one reconnect capability.

## Documentation maintenance

Update the Google PRD delivery status, export `docs/Study-Buddy-PRD.pdf`, and update both READMEs together when scope or merged status changes. The PDF is a snapshot, not automatic synchronization. Review PR state against `dev`, not an unmerged local branch. Preserve P1 requirements as planned until implemented and evaluated.

The old chronological build log is preserved in [archived development notes](../docs/archive/backend-development-notes-2026-10-26.md); earlier “future step” statements there are historical, not current limitations.
