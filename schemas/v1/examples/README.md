# Handoff fixtures (v1)

These hand-written development fixtures remain available for offline contract tests and the local terminal prototype. Live Tutor/Assessment adapters and the Reviewer gate are now implemented in merged dev; these files are examples, not live outputs or proof of content quality. They use the approved `lessons/cell-membranes.json` lesson and the unchanged v1 handoff schemas. See [current project status](../../../README.md) for implemented, under-review and planned work.

| File | Purpose |
| --- | --- |
| `tutor_to_assessment.example.json` | Full first-round explanation teaching all seven approved concepts; input for generating a 10-question quiz. |
| `assessment_to_tutor.example.json` | Existing return handoff: 6/10 and two weak concepts requiring reteaching. |
| `tutor_to_assessment.retry.example.json` | Revised explanation teaching only those two weak concepts; input for one focused 10-question retry. |

## How Soma can use them

Load the first-round fixture as Assessment input. Generate exactly 10 multiple-choice questions, with four options (a–d), one correct answer, and one taught concept ID per question. Keep answer keys private to the controller. A concept appearing in the approved lesson alone is not enough: it must also be taught in the current explanation.

The retry fixture follows the existing return handoff in the same session. Its focus IDs are exactly the returned weak concept IDs, and its explanation ID and handoff ID differ from the first round. It does not include the private answer key or student answers. Repeating concepts across 10 questions is permitted; do not require 10 distinct concepts or add untaught content to fill the quiz.

Example IDs such as `session_0001` are documented placeholders, not production-generated IDs. The controller owns production IDs and retry limits. Neither fixture makes real Claude calls or writes progress to the database.

## Coverage and boundaries

The first fixture covers amphipathic phospholipids, saturated/unsaturated tails, fluidity factors, cholesterol buffering, the fluid mosaic model, membrane asymmetry, and glycocalyx recognition. The retry covers only tail saturation and cholesterol buffering.

Do not quiz membrane protein subtypes, lipid rafts, numerical fluidity predictions, enzyme mechanisms, or historical dates: those details are not taught in the fixture. A schema-valid handoff still needs a semantic check for explanation/concept consistency.

## Source and attribution

Adapted and simplified from *Cell & Molecular Biology*, section 2.1, by Marg Sonnenfeld and Jonathan (Jung Lynn) Jung, Thompson Rivers University: https://cellandmolecularbiology.pressbooks.tru.ca/chapter/2-1/

The lesson content, explanation text, and taught summaries are adapted under CC BY-NC-SA 4.0: https://creativecommons.org/licenses/by-nc-sa/4.0/. Changes: beginner-friendly paraphrasing, selected concept coverage, and a focused reteaching example. No source figures or third-party media are included.

Issue: https://github.com/Soma-Majumder/Study-Buddy/issues/9
