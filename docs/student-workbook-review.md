# Student workbook review

This change connects a redesigned local workbook to the existing Tutor, Assessment, Reviewer and controller. It does not introduce an additional agent or public-account authentication.

## Student journey

Landing → lesson selection → original textbook reading → deliberate AI preparation → saved Tutor explanation while the quiz prepares → published 10-question quiz → confirmation → server grading and Assessment feedback → at most one focused retry if below 8/10 → saved summary.

The original chapter opens on the publisher's site. Local adapted teaching excerpts are optional and labeled; they are not presented as the full original chapter. New biology source files retain source attribution and non-commercial ShareAlike terms. They are introductory selections, not full textbook imports or claims of human content approval.

Only explicit preparation, feedback and retry actions can dispatch paid adapters. Browsing, reading saved state and reconnecting do not. Live mode has no fixture fallback. Unapproved candidates and answer keys stay private until the appropriate submission boundary.

## Changes to review

- Gold/neutral styling, responsive reading and quiz screens, reduced-motion support, and preparation messaging below the reading.
- Selected chapter and view restoration; URL hash navigation; lesson-bound start request IDs; distinct resume and new-session actions.
- API version/capability negotiation. An older Python process is detected instead of blindly calling unsupported progress endpoints.
- Owned read-only preparation progress and early saved Tutor explanation, including a retry explanation before retry publication. Feedback/reteaching and final-save stages are distinguished.
- Safe failure messages for truncation, exhausted content revision, invalid reviews, expired preparation and invalid actions, while keeping saved reading available.
- Workbook startup and periodic maintenance reconcile expired operations without dispatching AI. Polling rejects pre-mutation snapshots and late stale responses, releases mutation controls, and resolves terminal saved routes. Opening the site without a route goes to the landing page.
- Three additional biology selections. Offline fixtures remain limited to the original membrane lesson; live mode accepts the approved catalog. Missing lesson versions are added to existing workbook databases without replacing referenced records.
- Haiku 5.5 quiz generation/revision opts into its supported thinking-disabled mode for isolated structured output. Other models keep their defaults; output allowance is not increased and no extra paid retry is added. Reviewer still independently checks quality.
- Reviewer instructions distinguish confirmed ambiguity or contradicted justifications from genuine unresolved evidence. Controller uncertainty/publication policy is unchanged; authored regression tests cover both paths.

## Review without spending

Use the project's existing locked Python environment and backend dependencies; see `LOCAL_CHECKS.md` for installation and platform limits. From the repository root:

```sh
python3 -B scripts/check_workbook_inprocess.py
node --test tests/student-journey.test.cjs tests/student-start-routing.test.cjs
python3 -B scripts/check_backend_offline.py
```

The first command uses real handlers, controller actions and disposable SQLite through an explicitly in-process transport, with fixture agents. It strips provider credentials and disables paid calls. It does not bind a port or verify a browser.

For an isolated browser rehearsal, use a separate offline database and port:

```sh
python3 -B scripts/student_workbook.py --agents offline --port 8780 --database /tmp/study-buddy-review.sqlite
```

Open `http://127.0.0.1:8780/student/#library`. Verify chapter refresh, start/resume, quiz answer edits and confirmation, submitted feedback, pass/one-retry summary, and summary refresh. Offline content/review is synthetic; this is coordination evidence, not live AI quality evidence.

## Verification limits

23 real HTTP workbook tests, 23 in-process checks, and 116 JavaScript tests passed. An offline browser walkthrough completed landing, source reading, ten answers, 3/10 feedback, reteaching, one ten-question retry, another 3/10 result, and a saved final summary with one retry used. Deliberate race cases are checked with regression tests, not manually timed clicks. The live server was restarted and its abandoned session reconciled to a deadline failure. Restart an existing Python workbook process after pulling backend changes; refreshing only reloads static assets.

Saved user-initiated live runs include both approved quizzes and blocked generations/reviews. Those observations do not establish complete live success across all chapters or through feedback, retry and summary. No fresh paid evaluation was run while preparing this review. Do not mark the system complete based on fixture results.

Local databases, raw traces, generated audit reports, screenshots, credentials and machine-specific launcher shortcuts are excluded from the PR. Existing saved sessions are not deleted or rewritten.
