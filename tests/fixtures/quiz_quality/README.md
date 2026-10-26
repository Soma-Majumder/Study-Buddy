# Reviewer contract fixtures

These synthetic reviewer responses test contract mechanics, not whether a model can judge quality. The pass fixture contains authored pass judgments and demand labels; it is not independent educator approval of the manual quiz. The manual revision is a proposed reference, not an established gold standard.

Load context.json as trusted test context. Each case contains report and expected_decision; candidate_override, when present, replaces only the context candidate before validation. pass expects pass; ambiguous_answers and inaccurate_rationale expect needs_revision; uncertain expects blocked; stale_hash expects ReviewRejected because its report approved an older snapshot. All reports have valid structural shape, including the intentionally stale hash.

Context/candidates contain backend-only keys. Support quotes come from the supplied Tutor explanation. No model calls, database migration, or live gate are involved. These fixtures must never be returned by a learner endpoint.

## Benchmark cases (benchmark_*.json)

Labelled defective or policy-edge candidates for a future live Reviewer evaluation. Each has `known_defect`, `source`, an authored `report`, `expected_decision`, and `expected_reasons`; `candidate_override` replaces the whole context candidate. `tests/test_quality_benchmark_fixtures.py` checks only that the controller derives the expected decision and reasons from the authored report. It does not show that a live Reviewer finds the defect: the next step is to feed each candidate to the Reviewer and compare its verdict with these labels.

Cases: weak_distractors_category_cue, overlapping_true_narrow_vs_broad, repeated_target_bilayer, unsupported_mechanism, recall_heavy_all_items_pass, plus three threshold cases (understanding_boundary_five, strict_labels_reference, understanding_below_minimum_three). strict_labels_reference shows the manual reference quiz scores four under strict demand labels (Q4 as recall, Q9 as evidence_boundary).

Gap: there is still no independent good case beyond pass.json, and only one lesson. Add a good reference quiz from a second lesson, reviewed by an educator, before measuring unnecessary rejection.

Policy: six understanding questions is the generation goal (`UNDERSTANDING_GOAL`); the gate blocks only below four (`MINIMUM_UNDERSTANDING`). understanding_boundary_five (5) and strict_labels_reference (4) pass; understanding_below_minimum_three (3) and recall_heavy_all_items_pass (2) need revision.
