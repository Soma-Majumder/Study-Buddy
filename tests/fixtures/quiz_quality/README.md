# Reviewer contract fixtures

These synthetic reviewer responses test contract mechanics, not whether a model can judge quality. The pass fixture contains authored pass judgments and demand labels; it is not independent educator approval of the manual quiz. The manual revision is a proposed reference, not an established gold standard.

Load context.json as trusted test context. Each case contains report and expected_decision; candidate_override, when present, replaces only the context candidate before validation. pass expects pass; ambiguous_answers and inaccurate_rationale expect needs_revision; uncertain expects blocked; stale_hash expects ReviewRejected because its report approved an older snapshot. All reports have valid structural shape, including the intentionally stale hash.

Context/candidates contain backend-only keys. Support quotes come from the supplied Tutor explanation. No model calls, database migration, or live gate are involved. These fixtures must never be returned by a learner endpoint.
