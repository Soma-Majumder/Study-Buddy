# Session-start request reservation — local prototype

This is a local, unwired controller prototype on `codex/session-start-reservations`. It does not change `start_session`, `start_quiz_session`, any agent prompt, shared `db/schema.sql`, or the numbered migration manifest. No new PR is created and PR #47 remains the reconnect review.

## Record and contract

The proposed table is `db/proposals/session_start_requests.sql`, applied explicitly only to disposable test databases. It stores learner ID, client request ID, pinned lesson ID/version, unique session/handoff/explanation IDs and the UTC reservation timestamp. `(learner_id, request_id)` is the primary key. A request ID is an opaque 8–128 character ASCII letter/digit/underscore/hyphen token. A client reuses it for the same Start action after a timeout; a deliberate new session uses a new token. Identity must come from trusted authentication context.

`reserve_session_start(conn, learner_id, request_id, lesson_id, lesson_version)` requires an explicit approved lesson version, foreign keys enabled and no pending caller transaction. Under `BEGIN IMMEDIATE` it reserves the request, creates its teaching session and first outgoing handoff/repair record atomically, and commits before returning a grant. Each fresh request pins its version rather than silently adopting a newer lesson. No explanation row exists until future Tutor work succeeds.

Only a newly inserted request returns `reserved=True`. Exact replays return `reserved=False`, the same IDs/timestamp and current saved session status. Reusing the token for another lesson/version conflicts. Another learner has a separate request namespace. Concurrent duplicates serialize under SQLite's write lock and grant only one caller permission to dispatch. An insertion failure rolls back all new records.

## Crash and integration boundary

A lost response or crashed caller does not release the reservation or grant another model call. Replays never automatically resume work, even if the first process died before dispatch; that conservative choice avoids uncertain duplicate paid calls. This step alone does not reconcile abandoned Tutor starts or implement durable provider-call completion. Those stopping/recovery rules must be added before production wiring.

Before integration, review this shared record contract with Soma, choose a numbered migration and baseline/fresh-schema strategy, and modify the start entry point to use the reserved IDs without calling the old create-session path a second time. Only the reservation winner may invoke Tutor and downstream work; duplicate callers should retrieve the existing session. Keep database transactions closed during model calls. Do not deploy this proposal SQL against an application database as part of local testing.

Offline tests cover identical replays, namespace/input conflicts, terminal-state replay, explicit new requests, invalid/unapproved inputs, foreign-key requirements, caller-work preservation, rollback and simultaneous identical/conflicting requests. Fixtures use no model calls.
