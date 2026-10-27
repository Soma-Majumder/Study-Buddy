# Reviewer contract fixtures

Status reference (October 26, 2026): these are offline fixtures for the implemented backend gate. They do not establish live Reviewer reliability or student outcomes. See [project delivery status](../../../README.md); planned P1 features are separate from this v1 rubric.

These synthetic reviewer responses test contract mechanics, not whether a model can judge quality. The pass fixture contains authored pass judgments and demand labels; it is not independent educator approval of the manual quiz. The manual revision is a proposed reference, not an established gold standard.

Load context.json as trusted test context. Each case contains report and expected_decision; candidate_override, when present, replaces only the context candidate before validation. pass expects pass; ambiguous_answers and inaccurate_rationale expect needs_revision; uncertain expects blocked; stale_hash expects ReviewRejected because its report approved an older snapshot. All reports have valid structural shape, including the intentionally stale hash.

Context/candidates contain backend-only keys. Support quotes come from the supplied Tutor explanation. No model calls, database migration, or live gate are involved. These fixtures must never be returned by a learner endpoint.

## Benchmark cases (benchmark_*.json)

Labelled candidates for a future live Reviewer evaluation. Each has `known_defect`, `source`, an authored `report`, `expected_decision`, `expected_reasons` (hard blocks) and `expected_warnings`; `candidate_override` replaces the whole context candidate. `tests/test_quality_benchmark_fixtures.py` checks only that the controller derives those values from the authored report. It does not show a live Reviewer finds the defect: the next step is to feed each candidate to the Reviewer and compare its verdict with these labels.

Policy (Soma's decision): wrong, ambiguous, ungrounded, cued, weak-distractor or uncertain items block the quiz. `insufficient_understanding` (fewer than six understanding items, the generation goal) and `repeated_target` are warnings, as is `answer_cue` when no individual question criterion failed (an answer cue on a failing question still blocks): the quiz is still published and the warning is returned for monitoring.

Hard cases: weak_distractors_category_cue, overlapping_true_narrow_vs_broad, unsupported_mechanism. Soft cases (pass with warnings): repeated_target_bilayer, recall_heavy_all_items_pass, understanding_boundary_five, strict_labels_reference (the manual reference quiz scores four under strict demand labels), understanding_three, answer_cue_quiz_level.

Gap: there is still no independent good case beyond pass.json, and only one lesson. Add a good reference quiz from a second lesson, reviewed by an educator, before measuring unnecessary rejection.
