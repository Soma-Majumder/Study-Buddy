## Quality reference: lessons from the manually revised quiz (v1)

This harness-owned guide describes design patterns, not lesson facts or ready-made questions. Apply it only to the current explanation and taught/focus concepts. Never copy questions from another session or expand retry scope. Do not output this guide, a plan, or extra fields.

### Plan ten distinct targets before writing options

Use a compact internal plan: taught claim, concept_id, reasoning task, and what the learner must infer. Do not output the plan. Choose at least six supported understanding tasks across comparison, cause_effect, prediction and misconception_correction; use at most four recall items. Prefer a mix of task types instead of ten variations of “what is X?”. No fixed count per reasoning type is required, especially for a narrow retry focus.

For each understanding item:
1. Identify the exact relationship supplied in the explanation, including its conditions and qualifiers.
2. Give a small new case that requires applying that relationship: compare otherwise-equal cases, change one taught factor, or evaluate a mistaken interpretation.
3. Ask for the inferred comparison, outcome, or correction. The case information must matter to selecting the answer.
4. Build parallel choices on the same variables and verify one defensible answer using only the explanation.

A definition, list, named part, or direct restatement remains recall. Adding “why”, changing a noun to “a student”, or placing a remembered fact in a story does not make it an understanding question. If the student can ignore the case information and simply repeat one lesson sentence, rewrite the task.

### Transfer the relationship, not the lesson wording

These abstract patterns illustrate question design, not factual lesson content or stems to copy. Use the learner's subject and plain language in the actual quiz; never output the placeholder letters.

- Comparison: if the teaching links more R to less S with other conditions equal, describe two cases with different R and ask which S comparison follows. All four choices compare those same cases.
- Prediction: change a stated cause in a supplied case and ask for the direction of its taught effect. Do not ask for an exact number, an unprovided mechanism, or the effect of an untaught factor.
- Misconception correction: give a plausible reversal of a taught relationship or an overstatement of its qualifier, and ask which correction fits that case. Every choice addresses the same mistaken claim; avoid one careful answer against three absurd absolutes.
- Cause and effect: supply an observed outcome and ask which of the taught differences explains it, with the relevant conditions specified. Do not treat an effect as proving a unique cause unless the explanation warrants that inference.

A supported definition can also be applied to unfamiliar described cases. Merely asking the definition again is recall. Distinguishing known evidence from an unspecified detail is evidence_boundary, which does not count toward the six understanding items. Do not use it to fill the reasoning quota.

### Avoid repeating a target

Track the relationship tested, not just concept_id or wording. A second item on the same concept must test a different taught relationship or consequence; simply reversing the original stem, renaming cases, or asking for the same fact again is duplication. First-round coverage and retry focus still apply. In a narrow retry, use distinct supported facets of the focus concepts; do not borrow other concepts to reach ten questions.

Grounding takes precedence over the question mix. Do not force six understanding items by inventing facts, unjustified causal claims, or extra teaching. If fewer than six distinct understanding tasks are supported, use the strongest grounded mix available; never relabel recall as reasoning. If ten distinct, sound questions cannot be made at all, use the documented insufficient-content response.

### Make the wrong choices useful

Keep all choices on the same variables. For a two-variable relationship, vary those variables across the four choices instead of introducing unrelated entities. For a two-condition comparison, describe both conditions in every choice. For a definition applied to two cases, every choice should address those same cases.

Choose distinct wrong claims: reverse a taught direction, swap taught roles, omit a needed condition, or strengthen a qualifier beyond the evidence. Verify that each wrong claim conflicts with the teaching in this specific stem. Reject redundant wrong choices as well as equivalent correct choices. Never use a narrower true statement against a broader correct summary.

### Review the whole quiz internally

For each drafted item, classify its actual demand internally. Count only comparison, cause_effect, prediction and misconception_correction toward the six-question goal; recall and evidence_boundary do not count. Inspect the full quiz before returning: replace avoidable recall items and repeated targets with unused supported reasoning tasks. Check whether two items test the same relationship despite different wording. Balance option detail so the only careful or long answer is not repeatedly correct. Keep rationales to one grounded sentence of at most 25 words; do not invent a mechanism to make a recall rationale sound deeper.

If any item fails, replace it within this same generation before returning. This is a self-check, not an extra model call or a new repair budget. Return the existing insufficient-content response if ten distinct supported items cannot be made. The controller still owns validation and the single technical repair.
