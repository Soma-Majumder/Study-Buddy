# Study Buddy backend guide

Current operation as of October 26, 2026, based on merged `dev` commit `8c53b0d`. Use the [project README](../README.md) for the product overview and delivery-status table. The [Google PRD](https://docs.google.com/document/d/1MuA6lEJigDXQGIhVv3FKvgnLPPqIrBOd5FBY1T0qZXY/edit?tab=t.6wuguzynoy44) is the product source of truth; [the PDF](../docs/Study-Buddy-PRD.pdf) is its dated export.

## Implemented, under review and planned

**Implemented in dev:** approved lesson loading; Tutor teaching/reteaching; Assessment quiz generation and result explanations; Reviewer dispatch, bounded content revision and exact-candidate publication; deterministic grading; one learning retry; saved attempts/feedback/weak concepts and final summaries; learner-scoped ownership; duplicate-submission replay; paid-call opt-in; crash expiry; maintenance worker; migration tracking/baselining/execution. This is backend functionality, not a production deployment.

**Under review:** [PR #49](https://github.com/Soma-Majumder/Study-Buddy/pull/49) adds feedback/reteaching deadlines (migration 010), expiry sweeps for abandoned starts and agent operations, next-action guidance and an offline controller lab. Do not assume they exist on `dev` until merged.

[PR #50](https://github.com/Soma-Majumder/Study-Buddy/pull/50), based on #49, adds prompt-only Assessment and Reviewer quality guidance, a dedicated revision prompt and opt-in token-usage capture. Live quality is not yet proven.

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

`db/schema.sql` is the full current application schema. Numbered migrations are incremental additions and do not bootstrap the original base tables. Create a new database with `python -B scripts/init_database.py /absolute/path/to/new-study-buddy.db` (new path only, owner-only permissions, full current schema, foreign keys verified), then load approved lessons and explicitly adopt a verified baseline before using migration tracking. Existing partially upgraded or customized schemas require manual review; do not guess adoption from table names.

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

Commands open existing databases only and refuse backup overwrite. Backups use SQLite's backup API and owner-only permissions. The backup is written after the plan or preview is re-verified and while the write lock is held, so it contains every committed write and a rejected plan creates no file; a backup that itself fails is removed so the path can be reused. A backup kept after a failed migration is intentional. Execution rechecks history/files under `BEGIN IMMEDIATE`, executes statements individually, records exact fingerprints and commits the batch together. Failures roll back SQL and history. Unsupported transaction controls, PRAGMAs, attached databases, temporary objects/triggers, extensions or metadata modifications are rejected. This is trusted migration tooling, not an arbitrary-SQL sandbox. Keep backups on failure; no application database was migrated as part of documenting these commands.

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


### Local follow-up: abandoned session-start expiry

The maintenance cycle now runs both quality-operation expiry and reserved-start expiry. `--limit` bounds each sweep separately (at most twice that many records per cycle). Reservations with no first-round quality operation or quiz expire 120 seconds after their saved creation timestamp while still teaching. The worker records `session_start_deadline`, preserves the request claim and all budgets, and never dispatches agents. Controller checks discard late Tutor results.

Counts add `session_starts_checked` and `session_starts_expired`; `sessions_failed` includes both kinds. Each sweep owns a separate committed batch. Failure propagates even if the first batch already committed; the existing bounded lock retries safely repeat idempotent sweeps. Migration 009 must be installed before using this updated worker. No worker service or application database migration was installed as part of local testing.
## Read-only session status (PR #47, merged)

`controller.get_session_status(conn, trusted_learner_id, session_id)` returns an owned-session snapshot using one SELECT: `session_id`, actual saved `status`, display `stage`, `learning_retry_count`, `quiz_available`, active `quiz_id` (or null), `summary_available`, and a fixed safe `message` (or null). Ownership failures and unknown IDs use the same `SessionStatusRejected` message. The caller supplies authenticated identity; this helper does not implement authentication or a public HTTP endpoint.

Quiz availability requires `awaiting_answers`, the latest quiz's saved publication binding and no saved attempt. Completed summaries are advertised only when their row exists. Actual state is reported without inferring completion, invoking agents, retrying saves or sweeping deadlines. Raw failure reasons, questions, keys, candidate data, feedback and summary bodies are not read or returned. Availability is metadata: actual quiz retrieval must still pass controller publication/ownership checks. The query neither commits nor rolls back caller work and also works on a read-only SQLite connection. Preparing operations abandoned past their deadline remain in their saved state until the separate expiry process reconciles them.

## Read-only current quiz retrieval

`controller.get_current_quiz(conn, trusted_learner_id, session_id)` returns `{session_id, status, quiz, next_step}` for an owned session awaiting answers. The quiz contains only quiz/question IDs, positions, prompts and a–d choices. Ownership is checked before private evidence. The latest quiz must be unanswered and have a saved publication binding matching its operation, session, lesson/version, teaching/round and retry count. Existing approval/report/generator fingerprints and exact published questions/private keys are rechecked internally before projecting the learner response. Private audit records and answers are never returned.

Reads share a SQLite snapshot via a nested savepoint; this supports read-only connections, makes no application writes and preserves an existing caller transaction on success or rejection. It does not publish, dispatch agents, alter budgets or sweep expiry. An already published quiz remains retrievable after its preparation deadline. Wrong-owner, missing, stale, unreviewed or inconsistent requests raise a fixed `QuizRetrievalRejected` message; database infrastructure errors propagate. This is a controller callable, not a public HTTP/authentication implementation. The raw `answer_keys.get_client_quiz` remains an internal projection and must not be exposed without these checks.


## Read-only completed summary retrieval

`controller.get_saved_summary(conn, trusted_learner_id, session_id)` reads an owned completed session and its existing summary without calling `finish_session`, agents or grading. It validates the saved JSON identity, lesson/version, final attempt, scores, rounds and weak concepts against scoped persisted evidence, then explicitly projects approved summary/lesson/attempt/concept fields. Unexpected JSON fields are omitted and the learner-facing disclaimer is fixed; no raw arbitrary stored message, answer keys or private review data are returned.

A missing, malformed, inconsistent or non-completed summary raises the same `SummaryRetrievalRejected` message as an unavailable/wrong-owner session. Database errors propagate. All reads share a snapshot via a nested savepoint, support a read-only connection and preserve pending caller work. This is a controller callable, not authentication or a public HTTP endpoint. Summary retrieval and the persisted reconnect integration tests are included in PR #47 alongside status and current-quiz retrieval as one reconnect capability.


## Combined controller package (PR #49)

### Combined controller verification

`codex/controller-integration` combines the proposed summary recovery (PR #45), reconnect reads (PR #47), session-start reservations (PR #48), and local deadline/maintenance follow-up. This is a local verification branch, not a merge into dev or main and not a replacement review request.

Run `python -B -m unittest tests.test_controller_integration -v` from the repository root with development dependencies installed. Five offline integration scenarios create disposable file-backed databases, verify baseline 008, apply migration 009, and exercise reservation replays, duplicate answers, read-only reconnect, the 8/10 passing threshold, one focused retry, summary-save recovery, and abandoned-start expiry. Every reconnect checks owner isolation and approved response fields; fixture counters verify that reads and replays do not restart agents. Maintenance is tested alongside successful/retrying/completed sessions as well as abandoned starts. Fixtures prove controller behavior, not generated educational content quality.

Validation on 2026-10-27: 457 Python tests and 95 JavaScript adapter/harness tests passed. Python tests emit existing SQLite ResourceWarnings; the suite has no failing tests. No paid calls, application database migrations, deployed worker, pushes or PR updates were performed for this integration step. Review/merge of pending PRs, deployed identity/API wiring, process supervision and a separately authorized live-agent check remain outside this local verification.


### Offline controller scenario menu (local)

From the repository root, run:

```sh
python3 -B scripts/offline_controller_demo.py
```

Choose 1–5 for pass (8/10), practice (6/10 then one 10/10 retry), failed summary save and recovery, abandoned-start expiry, or duplicate Start/answer submissions. Choose `a` to run all or `q` to quit. For a noninteractive rehearsal use `python3 -B scripts/offline_controller_demo.py --scenario all`; individual names are `pass`, `practice`, `recovery`, `crash`, and `duplicates`. Python development dependencies from requirements-dev.txt must be installed; the demo needs no Node runtime or API key and does not load .env.

Each scenario uses a new temporary database, the current full schema and approved lesson fixture. Agents are explicit deterministic callbacks from scripts/offline_controller_fixtures.py; simulated learner answers use fixture keys internally without displaying them. The tool exercises real controller grading, routing, persistence, recovery, read-only reconnect and maintenance. It prints agent activity, the saved controller trace, final status, record counts and fixture-call counts, and removes the database on completion or failure. There is deliberately no application database path argument.

The Reviewer report is synthetic and retry questions may repeat fixture questions to fill ten slots; this demonstrates controller mechanics, not educational quality or live provider behavior. Crash expiry is demonstrated by backdating only the disposable reservation. The duplicate scenario checks an in-progress and completed Start replay, original-attempt reuse and changed-answer rejection. The crash scenario intentionally ends failed and counts as a successful demonstration when that failure is saved correctly. Ordinary tests forbid provider processes and network connections while exercising all scenarios, including when paid-call opt-in is set. No prompt, shared schema, production database, or PR is changed by running the demo.


### Local browser controller lab

Run `python3 -B scripts/offline_controller_ui.py` from the repository root, then open http://127.0.0.1:8765. Use `--port 8766` if that port is occupied. Ctrl+C stops the server. The page shows the agent flow, five scenario buttons, a run-all action, scores, fixture-call counts, saved record counts, a controller trace and a run checklist. Each click executes the same offline scenario runner described above; the API reports success only after its state assertions and temporary database cleanup complete. An intentionally expired session displays “Failed safely” with a verified scenario badge.

This is a developer testing interface on the local integration branch, not the learner application or its public API/authentication implementation. The Python standard-library server binds only 127.0.0.1, serves three allowlisted assets, requires the local Host and a page-provided token for scenario POSTs, rejects foreign origins, and accepts only a scenario name. There is no database path, prompt, credential or paid-call option. Responses do not include private keys or raw exceptions. All fonts/assets are local. A fresh database is created and removed per request; results are displayed in browser memory and disappear on refresh. The server handles scenarios serially, and the page disables actions while a run is active. No service is installed or deployed.

Verification: all five scenarios passed through the actual browser interface, plus 468 Python tests passed including HTTP/API, invalid input, local origin/host/token checks and safe failure responses. The interface remains local; no pending PR was updated.


### Local follow-up: session-status next-action guidance

`get_session_status` adds `next_action` to its existing allowlisted response, derived from the same read-only snapshot:

| Saved state | Advisory next_action |
| --- | --- |
| started, teaching, grading, reteaching | read_session_status |
| awaiting_answers with available published quiz | get_current_quiz |
| awaiting_answers without available quiz | contact_support |
| pending_save | resume_summary |
| completed with saved summary | get_saved_summary |
| completed without summary | contact_support |
| failed | start_new_session |

These strings are guidance, not automatic dispatch or new endpoints. Retrieval helpers still verify ownership and saved evidence; availability metadata does not validate all quiz/summary contents. Resume must use the existing trusted learner/session identity and recheck readiness; a deliberate new session needs a fresh request ID. Checking status does not retry a provider call, sweep deadlines, retry a save, create a session, or read raw failure reasons/private content. Waiting-state guidance does not provide a polling interval or promise recovery for abandoned work. `contact_support` is an interface fallback, not an implemented support service. Local follow-up only; PR #47 and other pending reviews are unchanged. The offline browser UI displays this field as described below.


### Local interface: controller recommendations

The offline runner returns `next_action` from the final read-only `get_session_status` response, plus a small `guidance_history` of actual status responses at quiz readiness, failed-summary saving (recovery scenario), and final reconnect. Each checkpoint projects only its label, saved status and advisory action. The page displays “Controller recommends” with plain-language text and the action identifier; “Recommendations during this run” explains intermediate guidance, including `resume_summary` before recovery completes. Unknown actions use an unavailable-guidance fallback.

Recommendations are display-only. The temporary session database has already been removed when the browser receives the result, so no recommendation button attempts to operate on it. A new scenario run creates a new disposable session. All five API scenarios check the final recommendation; the recovery scenario verifies the sequence `get_current_quiz`, `resume_summary`, `get_saved_summary`. Browser verification confirmed the recovery history and final recommendation. 469 Python tests passed; no paid calls or PR updates.


### Local migration 010: feedback and reteaching timing

`010_agent_operation_deadlines.sql` adds two timing tables linked to existing feedback operations and retry routes. New feedback claims persist integer UTC epoch `started_at` and `deadline_at` in the claim transaction; their fixed window is 180 seconds for generation plus at most one technical repair. Reteaching timing starts when the controller reserves the learning retry, before Tutor dispatch, with a 120-second window. Both timing inserts are atomic with their claims. Timing-save failure rolls back the claim and any learning-retry reservation. Replays, feedback repairs and delayed dispatch never renew a deadline. Timing remains backend-only and does not enter agent prompts or handoff schemas.

Migration 010 gives legacy active feedback (`running` while grading) and reteaching routes (`reserved`/`dispatched` while reteaching) one adoption window starting at migration execution. Their original start time is unavailable: the backfill timestamp means adoption, not historical provider dispatch. It neither resets budgets nor resends calls. Completed/failed feedback and sessions are not backfilled. The migration and its history commit or roll back together.

Migration 001–009 bytes remain unchanged. Historical complete schema 009 is frozen at `db/baselines/009_schema.sql`, alongside 008; the baseline tool now explicitly supports 008, 009 and fresh schema 010. Tracked 009 databases apply pending 010 after backup and maintenance review. Untracked verified 009 databases adopt baseline 009, then apply 010; freshly initialized current databases adopt baseline 010. Existing 008 databases apply pending 009 and 010 after their verified baseline. Never run 010 over a fresh schema that already contains these tables.

Timing is enforced on controller activity by the local follow-up below. Periodic maintenance expiry is now wired as described below. No deployed database was migrated. This is local work on `codex/controller-integration`, with no push or existing PR update.

Verification for the timing change: 479 Python tests passed, including reopened-database durability, repair/replay invariance, atomic claim rollback, timing constraints, historical baseline compatibility, legacy active-work adoption, and migration-history rollback. Tests use offline fixtures only.


### Local follow-up: enforce feedback and reteaching deadlines

Controller entries reconcile existing running claims against the saved migration-010 deadline. At or after expiry, feedback records `feedback_deadline` and reteaching records `reteaching_deadline`, atomically failing the active operation and matching session. Saved grading evidence, learning-retry count, timing and existing repair reservations are preserved. Replays never grant another dispatch.

Checks run under a write lock before dispatch and result persistence; feedback also checks before reserving its single repair and before invoking that repair. Late valid output, invalid output and provider exceptions cannot revive expired work or replace its deadline failure. Feedback expiry leaves the deterministic grading feedback in results intact; it does not save the late generated explanation. Reteaching expiry saves no new explanation or Tutor handoff and leaves the learning retry consumed. Already ready feedback and saved reteaching handoffs are preserved; retry quiz preparation has its own quality-operation deadline. Missing timing blocks work rather than inventing a new window.

No transaction spans an injected agent callback. Existing provider adapter timeouts remain in place; this change rejects late output and does not cancel an in-flight provider request or dynamically shorten its timeout. Maintenance scanning for abandoned feedback/reteaching is now wired as described below. No agent prompts, shared handoff schemas, paid calls, application database migrations, pushes or PR updates are involved.


### Local follow-up: maintenance expiry for feedback and reteaching

`run_maintenance_cycle` now sweeps four categories in order: quiz-quality operations, session starts, feedback operations and reteaching reservations/dispatches. The operator's `limit` applies independently to each category, so a cycle handles at most four times that many selected records. Each category owns an atomic transaction; earlier committed categories remain durable if a later one fails. Errors propagate and repeating the cycle is safe. No call is resent and no timing, grading evidence, repair reservation or learning-retry budget is reset.

New counts are `feedback_operations_checked`, `feedback_operations_expired`, `reteaching_operations_checked` and `reteaching_operations_expired`; `sessions_failed` totals failures across all four categories. Overdue running feedback is eligible only while its matching latest attempt is grading. Reteaching is eligible only for the latest attempt with a consumed retry, no saved Tutor handoff and no retry quality operation taking over expiry. Ready, superseded, published and terminal work stays untouched. Missing timing on otherwise eligible active work is an explicit maintenance error, not permission to create a new deadline or dispatch a provider. Migration 010 is required; the worker does not install migrations.

The existing foreground worker and one-shot expiry command both use this cycle. No service or scheduler is installed automatically. Tests use disposable databases and verify exact boundaries, batch limits, replay, unchanged results/budgets, atomic rollback, all-category counts, and a worker sweep completing while feedback/Tutor callbacks are paused. Late callbacks cannot revive expired work. This change remains local; no paid calls, application database changes, pushes or PR updates.

### Combined controller review package

The `codex/controller-integration` branch packages the integrated controller for one review into `dev`. It includes the commits already proposed in PR #45 (summary resumption), PR #47 (read-only reconnect) and PR #48 (session-start reservation/migration 009), plus the follow-ups documented above. Earlier sections marked local describe their implementation history; this section describes the combined review scope. Those PRs overlap this branch and must not be treated as four independent implementations. Review this combined branch for the final behavior; the earlier PRs remain open as references until the team chooses the merge path. No merge into dev/main or deployment is performed by publishing this package.

Suggested review order:

1. Migrations 009/010, frozen baseline references 008/009, and atomic timing/claim persistence. Migration 001–009 bytes remain unchanged by the 010 follow-up. Legacy active feedback/reteaching receives an explicitly documented migration-time adoption window.
2. Session-start reservation/deadline checks; feedback/reteaching expiry and result-save fencing. Confirm no replay grants another uncertain paid call, and score/repair/learning budgets remain preserved.
3. Four bounded maintenance sweeps, per-category counts and limits, atomic batches and partial-cycle retry behavior.
4. Summary resumption, ownership/publication/summary retrieval checks and advisory next_action projection.
5. Offline CLI/browser lab and cross-feature regression scenarios. Demo actions use deterministic fixtures and a disposable database, not production routes or semantic quality judgments.

Validation: 500 Python tests passed on the integrated branch. Tests include first-round pass, exactly one learning retry, summary recovery, ownership isolation, duplicate starts/answers, migration adoption/rollback, deadline boundaries, and real concurrent late-result rejection. No live agents, production migrations or deployed workers were used. Paid end-to-end agent verification, learner-facing authentication/API integration and supervised deployment remain separate release work.

## Assessment and Reviewer quality follow-up (PR #50)

### Local Assessment quality follow-up (after combined controller PR)

`codex/assessment-understanding` strengthens Assessment generation guidance after the live run produced nine recall questions and one cause_effect question. The system prompt/reference now specify an internal ten-target plan, a six-understanding-question goal when supported, concrete abstract task patterns, necessary case information, distinct taught relationships, and a final demand/duplication audit. Grounding and exact retry scope take precedence over filling a reasoning quota. Recall must never be relabeled as reasoning or padded with a decorative story.

The existing builders share these instructions with generation, technical repair and content revision. No output schema, provider effort/budget, Reviewer rubric, controller publication policy or handoff contract changes are included. Warnings remain warnings; the prompt goal does not silently become a new blocking rule. The revised prompt has only offline verification so far; semantic improvement needs a separately authorized paid evaluation. See backend/evals/assessment-quality-rubric.md for the comparison checklist. This follow-up stays local and does not update PR #49 or Soma's existing review branches.

### Local Reviewer concision and usage diagnostics

The Assessment quality follow-up branch also narrows Reviewer report verbosity: shortest sufficient literal quotes, normally one per grounded item, concise actionable issues/uncertainty, and no commentary on passing checks. Required judgments, controller bindings and honest uncertainty are unchanged; the output allowance remains 8192 at low effort.

The gated controller evaluator adds an explicit `--capture-token-usage` mode that records aggregate metadata even when the transport rejects a truncated response. It labels generation, repair, review, revision and revised-review phases and does not record response text or private reasoning. See backend/evals/README.md for privacy, missing-metadata semantics and the difference between fixture and fixed-live-explanation comparisons. 503 Python tests and 95 JavaScript tests passed offline. Changes stay local; PR #49 is unchanged. A paid evaluation is still needed to assess truncation improvement.


### Local Assessment revision focus

Content revision now loads a dedicated `backend/prompts/assessment-revision-system.md` rather than inheriting the full generation planning prompt and quality reference. It directs Assessment to preserve sound items and their keys, prioritize concrete failed criteria, fix answer cues locally, and replace only redundant targets when a distinct supported alternative exists. Unflagged changes need a concrete correctness/scope or linked-key reason. Full-quiz checks remain mandatory, but do not call for a new ten-target design pass. Warnings do not silently become blocking policy or justify inventing content.

The adapter still validates original candidate/report bindings before a call and still returns the complete ten-question quiz plus private keys in the same result envelope. Grounding, focus limits, one correct answer, short rationales, insufficient-content failure, untrusted-data separation and controller-owned budgets remain explicit. No model, output limit (8192), low-effort setting, timeout, schema, publication policy or repair allowance changes are included. Generation and its technical repair continue using the existing generation/reference guidance; content revision and its technical correction use the new focused prompt.

This is local on `codex/assessment-understanding`; no paid evaluation or PR update is included. Offline adapter/contract tests do not establish that the model preserves sound items or avoids truncation. A separately authorized fixed-explanation evaluation with usage capture is needed to assess those outcomes.

## Session-start request reservations (migration 009, PR #48)

PR #48 (merged) adds `009_session_start_requests.sql` and the same table to the fresh `db/schema.sql`. It is merged into `dev` but is not a deployed schema change until migrated. Migration files 001–008 retain their exact contents. The previous complete schema is frozen in `db/baselines/008_schema.sql` so the baseline tool can explicitly verify either historical 008 or current 009, without assuming the new table already exists.

For an untracked original full-schema database, preview/adopt `--through-version 8` against the frozen reference, then obtain the pending fingerprint and execute 009 with a new backup. Already tracked 008 databases skip adoption and execute only pending 009. For a new database created from current `db/schema.sql`, preview/adopt `--through-version 9`; never rerun 009 over that table. Baseline 008 must record only migrations 001–008 even though 009 is now discoverable. Incorrect version/schema pairs are rejected. After deployment review, use the same explicit maintenance commands described above, keeping writes paused and backups recoverable. No application database was changed by the offline tests.

## Documentation maintenance

Update the Google PRD delivery status, export `docs/Study-Buddy-PRD.pdf`, and update both READMEs together when scope or merged status changes. The PDF is a snapshot, not automatic synchronization. Review PR state against `dev`, not an unmerged local branch. Preserve P1 requirements as planned until implemented and evaluated.

The old chronological build log is preserved in [archived development notes](../docs/archive/backend-development-notes-2026-10-26.md); earlier “future step” statements there are historical, not current limitations.


## Offline assignment verification

### One-command offline backend check

From the repository root, run:

```sh
python3 -B scripts/check_backend_offline.py
```

Use the project's Python environment with `jsonschema` installed. This command reuses four existing tests: first-round 8/10 pass and persisted reconnect, 6/10 then one 10-question retry and summary, retry exhaustion even at a low retry score, and an uncertain review that prevents publication/answer submission. It disables paid-call opt-in for its process and uses fixture agents and isolated SQLite databases. No Node runtime, API key, frontend or application database is required. Exit status is zero only if all three scenarios pass; failures, errors or skipped checks are not reported as success. This verifies coordination/persistence, not live educational quality.

The offline check also reads the actual controller-saved summary before test database cleanup and prints the first/retry scores, retry count, final concepts to revisit, and saved completion status. The blocked case explicitly reports no summary. These are simulated practice results, not mastery claims.


### Fixture evaluation scores

Run `python3 -B scripts/score_backend_fixtures.py` from the repository root. This runs the private canonical quiz/key character-length check at a strict ratio above 1.3, validates the existing quality fixtures and reuses four existing session scenario tests. Scores are saved with mode 0600 under ignored `backend/evals/results/`; no API key, frontend or paid calls are used.

The session denominator includes the intentionally blocked case: 3/4 completed sessions is distinct from 4/4 expected safeguard/scenario tests passing. Handoff counts measure fixture contract acceptance at Assessment→Reviewer and Reviewer→controller; a valid content rejection still counts as a successful report handoff, while stale reports do not. Other routes and real delivery are not measured. Per-agent revision contribution remains unmeasured because no paired agent revision is run. Cases can share candidate hashes, so their balance flags are not independent quiz samples.

`study_buddy/option_balance.py` is an offline diagnostic only: no flags means only that this length heuristic found nothing. Flags do not prove weak distractors or answer clues. The runtime quality gate, Reviewer judgment and existing revision budget are unchanged; feeding flags into a future revision is not implemented by this command. No answer letters, option text or rationales are logged in the score report.


### Assessment contribution from saved runs

Run `python3 -B scripts/score_assessment_revisions.py /absolute/path/to/private-run.json ...` with saved original agent-run reports. No providers are called. Supported evidence includes original/revised first-round runs, the targeted saved-agent-candidate revision test and the saved-first-quiz/live-retry run. Manual-review reports are not accepted as agent revision evidence. The command validates each complete pair against its unchanged Tutor handoff, candidate hashes, bindings and rubric version. Logs under ignored `backend/evals/results/` contain position/code metrics, not quiz keys or option text.

First historical sample: 0 recovered out of 6 attempted agent revisions; 4 complete reviewed pairs and 2 incomplete attempts (revision truncated or final review unavailable). None of the 4 complete pairs passed. The data spans changing prompts and includes correlated/reused original candidates; it is not an independent benchmark, randomized ablation, or estimate of future reliability. Manual first/retry fixes and their subsequent passing Reviewer checks are excluded.

Complete pairs report understanding count, changed positions, length flags, and blocking position/code findings no longer reported, still reported and newly reported. A newly reported issue is not proof that revision caused it. Revision usage is retained only when captured; empty metadata is unknown, not zero. No extra revision calls, runtime policy or paid tuning is enabled.


### Saved handoff observations

Run `python3 -B scripts/score_saved_handoffs.py /absolute/path/to/private-run.json ...` to inspect recorded Tutor/Assessment callbacks and feedback/reteaching routes. First sample: live Tutor→Assessment 5/5 accepted (all recorded repairs zero), live feedback→Tutor 1/1 accepted (repair count unknown). Replayed content is separate: 7/7 Tutor→Assessment and 1/1 feedback→Tutor. Live/replay labels apply to the boundary, not the whole parent session. No model calls are made.

Scores use observed downstream callbacks and saved routing steps. Available Tutor output schemas and feedback/reteaching concept-set agreement are checked, but original trusted inputs and complete return envelopes are missing from some logs; this is not independent reconstruction of every validation. Unknown repair counts are not zero. Quiz-only runs with no feedback routing contribute no return-handoff attempt. Historical sample sizes are small and correlated; downstream content rejection does not undo a valid handoff. Local mode-0600 logs live under ignored `backend/evals/results/`.

Assignment report and graph: [Study Buddy evaluation report](../docs/evals/Study-Buddy-Evaluation-Report-2026-10-27.html). Download/open this self-contained HTML in a browser; Print → Save as PDF if needed. Historical saved-run scoring requires your own private input reports; those reports and databases are intentionally not distributed in this PR. No paid calls are made by these tools.

### Backend closeout check — October 28, 2026

One authorized paid session completed in 170.4 seconds using live Tutor, Assessment, Reviewer and feedback calls. The first quiz needed one content revision for an unsupported claim, then passed review. A simulated learner scored 7/10, received reteaching, and scored 10/10 on a retry quiz that passed its initial review. Both approved quizzes were published, one learning retry was used, the final summary was saved, and SQLite reported no foreign-key violations.

Aggregate evidence: [live backend check](../docs/evals/backend-live-check-2026-10-28.json). The simulation uses private answer keys to choose test answers; scores do not measure a real learner. This is one successful run, not a reliability estimate or evidence that the option-length hint caused the improvement. Repeated-target warnings remain advisory. The proposed six-understanding-question goal still needs Soma's agreement before becoming product policy. Historical October 27 results remain unchanged.

The bounded Assessment revision now derives private option-length hints from its validated candidate. They are advisory, add no calls, do not authorize extra revisions, and never bypass Reviewer. No frontend changes or private raw logs are included. Offline checks passed locally: 512 Python tests, 96 JavaScript tests, and all three backend report scenarios.

### Migration limits and baseline maintenance

Migrations cannot use `PRAGMA` or `CREATE TRIGGER`, so SQLite's documented table-rebuild recipe (it needs `PRAGMA foreign_keys=OFF`) and trigger migrations do not run here; this is deliberate. Prefer `CREATE TABLE`/`CREATE INDEX`, `ALTER TABLE ... ADD COLUMN`, `RENAME COLUMN` and `DROP COLUMN`. A change that needs a table rebuild or a trigger is a separate, reviewed maintenance procedure with its own backup, not an exception to this runner. A migration that renames a table onto a protected `schema_migrations`/`schema_baselines` name is rolled back.

When adding migration NNN, first copy the current `db/schema.sql` to `db/baselines/<NNN-1>_schema.sql` (frozen, never edited), then update `db/schema.sql`. Baselining supports every frozen snapshot plus the newest migration against `db/schema.sql`; no code change or version constant is involved. A missing snapshot is rejected, never guessed.
