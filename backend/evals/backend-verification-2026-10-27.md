# Backend verification — October 27, 2026

## Reviewer policy clarification

Repeated targets and fewer than six understanding questions are warnings under the existing policy. Reviewer must not turn a known style shortfall into speculative uncertainty about publication. Genuine unresolved grounding, ambiguity, rationale, coverage or diversity judgments still block. Controller code, schemas, call budgets and publication checks are unchanged. A regression test combines repetition warnings with genuine grounding uncertainty and confirms the latter still blocks.

## What was verified

| Test | Evidence and outcome |
| --- | --- |
| Fresh live Tutor → Assessment → Reviewer | Valid ten-question quiz; one revision exhausted; final review blocked unsupported warm/very-fluid substitution. No retry quiz or summary fabricated. |
| Corrected saved first quiz, clarified Reviewer prompt | Pass; five understanding questions, insufficient-understanding and repeated-target warnings. One paid Reviewer call; no generation. |
| Saved first quiz → live feedback → live reteaching → live retry generation/review | First score 7/10 and validated feedback/reteaching; retry blocked after one revision. Summary correctly not saved. Six paid adapter invocations. |
| Manually corrected saved retry | Pass with eight understanding questions and repeated-target warning. One paid Reviewer call; exact snapshot hash below. |
| Saved approved first/retry snapshots → grading → live retry feedback → final summary | Scores [7,10], exactly one learning retry, two quizzes/attempts/results, one saved summary, completed session, zero foreign-key violations. One paid feedback call, no generation or Reviewer calls. Summary verified after reopening the isolated database and repeated completion returned the same summary. |

Final first candidate: `backend/evals/revisions/2026-10-27-manual-options-fix.json`.
Final retry candidate: `backend/evals/revisions/2026-10-27-manual-retry-q1-fix.json`.
Both are assistant-authored evaluation references with private answer keys, never learner payloads. They are not production defaults, automatic agent revisions or gold-standard educational judgments.

Approved first hash: `8612d83118f18a516ec63b1259ee94152e66ae7c9d3b993933bb5f821f4c4640`.
Approved retry hash: `e212348353ef73a27eaa555c0530ec92b5038b9af4c1a42826be663de3c02b13`.

The completion test replayed saved first feedback and reteaching through normal controller functions into a new isolated database, rebinding session identifiers and validating reports against the rebound snapshots. It did not revive the failed earlier session or override its verdict. Only retry feedback was live. These are staged checks, not one uninterrupted all-live success.

## Offline verification and limits

The earlier full local checkout passed 505 Python and 95 JavaScript tests before the clarification regression was added (the Python total includes local learner-preview tests). The clarification passed 16 validator tests and 15 Reviewer adapter/input tests. Counts do not imply a fresh run on a different merged revision; see PR update for latest focused results.

Fresh automatic first/retry generation reliability remains unproven: repeated live checks found new content issues. Successful manually corrected snapshots establish the controller completion path and live feedback compatibility, not general agent readiness. Final manual edits do not train or change the generation prompts. Keep genuine uncertainty blocking and the single revision limit intact.

Scope: no new agents, calls, schemas, runtime features or frontend changes. Raw private run logs, local databases, credentials and learner-preview assets are excluded. Structured aggregate evidence is in `backend/evals/backend-verification-2026-10-27.json`.
