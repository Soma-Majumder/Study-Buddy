# Session-start request reservation — local prototype

This is an opt-in controller change on `codex/session-start-reservations`. `start_session`, `start_quiz_session` and `run_study_session` now accept `request_id` and an explicit `lesson_version`. Calls with these arguments use the reservation; calls without a request ID retain legacy behavior and are not deduplicated. This branch includes migration 009 and an equivalent fresh-schema table; no agent prompt changes. This work is reviewed separately from PR #47, which covers read-only reconnect actions.

## Record and contract

The table is defined in `db/migrations/009_session_start_requests.sql` and included in `db/schema.sql`. These changes remain local and are tested only on disposable databases. It stores learner ID, client request ID, pinned lesson ID/version, unique session/handoff/explanation IDs and the UTC reservation timestamp. `(learner_id, request_id)` is the primary key. A request ID is an opaque 8–128 character ASCII letter/digit/underscore/hyphen token. A client reuses it for the same Start action after a timeout; a deliberate new session uses a new token. Identity must come from trusted authentication context.

`reserve_session_start(conn, learner_id, request_id, lesson_id, lesson_version)` requires an explicit approved lesson version, foreign keys enabled and no pending caller transaction. Under `BEGIN IMMEDIATE` it reserves the request, creates its teaching session and first outgoing handoff/repair record atomically, and commits before returning a grant. Each fresh request pins its version rather than silently adopting a newer lesson. No explanation row exists until future Tutor work succeeds.

Only a newly inserted request returns `reserved=True`. Exact replays return `reserved=False`, the same IDs/timestamp and current saved session status. Reusing the token for another lesson/version conflicts. Another learner has a separate request namespace. Concurrent duplicates serialize under SQLite's write lock and grant only one caller permission to dispatch. An insertion failure rolls back all new records.

## Controller integration and crash boundary

A lost response or crashed caller does not release the reservation or grant another model call. Replays never automatically resume work, even if the first process died before dispatch; that conservative choice avoids uncertain duplicate paid calls. This step alone does not reconcile abandoned Tutor starts or implement durable provider-call completion. Those stopping/recovery rules must be added before production wiring.

The opt-in path uses the reserved session/handoff/explanation IDs rather than calling the legacy session-creation path again. Only the winner calls Tutor and enters the quiz pipeline. Duplicate callers get `reserved=False`, the existing session ID/status, no handoff, and no quiz. The full-session wrapper reports `next_action=read_session_status` rather than prompting for answers or restarting agents. Retrieve existing learner-visible progress separately. Initial winning calls and legacy callers retain their existing result shapes; duplicate handling is explicit through the `reserved` flag. Tutor output is still validated against the pinned database lesson/version; mismatch fails rather than accepting a different lesson.

Before deployment, review the shared record and migration with Soma. Untracked original schema-008 databases explicitly adopt the frozen reference before executing 009; already tracked schema-008 databases apply only pending 009; fresh full-schema databases adopt version 009. A missing reservation table is a database error, never permission to fall back to a duplicate start. Caller identity remains trusted application input, not an authentication implementation. Keep database transactions closed during model calls. Do not apply this migration to an application database as part of local testing.

Offline tests cover identical replays, namespace/input conflicts, terminal-state replay, explicit new requests, invalid/unapproved inputs, foreign-key requirements, caller-work preservation, rollback and simultaneous identical/conflicting requests. Fixtures use no model calls.

Controller wiring tests cover same-ID replay, unchanged reserved IDs, no downstream work on duplicate quiz starts, terminal failure and process-loss replay, record-save failure before Tutor, a concurrent duplicate returning while the winner is in flight, and replay after a completed full study session. These are fixture-based integration checks, not live model runs.
