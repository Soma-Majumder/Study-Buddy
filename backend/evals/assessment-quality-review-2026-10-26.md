# Quality review of the schema-valid Assessment quiz

Run: `results/2026-10-26T19-10-34-267200+00-00-assessment-controller.json`. Reviewed October 26, 2026 against the supplied hand-written Tutor explanation and `assessment-quality-rubric.md`. No new generation or runtime changes. This is assistant review, not independent educator validation.

## Verdict: format-ready, needs content revision

All ten keyed answers and rationales appear supported by the supplied explanation. No clear competing correct answers were found within that reference. All seven taught concepts appear, and rationales are 9–13 words, one sentence each. These findings do not independently verify the underlying science or prove freedom from all ambiguity.

Only Q2 and Q3 clearly require applying a relationship. The other eight primarily recognize a definition, named factor, location, or stated role. This falls short of our six-understanding-item review target. It is not a PRD or runtime pass/fail threshold. The ten-question format requirement is unchanged.

## Per-question rubric

G = grounded key/rationale; A = one defensible answer; D = plausible distractors; C = clarity/fair cues; R = useful explanatory rationale. Pass/fail are manual judgments. An R failure on a recall item means the question should first be redesigned; do not invent unsupported mechanisms to lengthen its rationale.

| Q | Concept | Demand | G | A | D | C | R | Finding |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | `c_amphipathic_phospholipids` | recall | Pass | Pass | Fail | Pass | Pass | Wrong choices a and d both place heads toward tails; redundant distractors reduce the useful alternatives. |
| 2 | `c_amphipathic_phospholipids` | cause/effect | Pass | Pass | Fail | Pass | Pass | Sugar attraction and surface-protein binding are unrelated explanations absent from the teaching; overlaps Q1 on bilayer arrangement. |
| 3 | `c_fatty_acid_saturation` | cause/effect | Pass | Pass | Fail | Pass | Pass | Packing reversal is plausible, but leaflet flipping and attracting water are weak alternatives; this is the strongest reasoning item. |
| 4 | `c_fluidity_factors` | recall | Pass | Pass | Fail | Pass | Fail | Asks for the memorized list; three distractor lists mix unrelated processes. Rationale just repeats the list. |
| 5 | `c_cholesterol_buffer` | recall | Pass | Pass | Fail | Pass | Fail | One useful reversed-motion distractor, plus unrelated sugar/leaflet roles. Rationale restates the keyed role. |
| 6 | `c_cholesterol_buffer` | recall | Pass | Pass | Fail | Pass | Fail | Clear cold-condition answer without the earlier synonym overlap, but identical leaflets and glycocalyx alternatives are implausible. |
| 7 | `c_fluid_mosaic_model` | recall | Pass | Pass | Fail | Fail | Fail | Single continuous sheet describes structure, not a parallel kind of movement. Rationale repeats move sideways. |
| 8 | `c_membrane_asymmetry` | recall | Pass | Pass | Fail | Fail | Fail | Correct choice is the only enzyme category; this still tests recognition of a specialist-name cue rather than the movement/help relationship. |
| 9 | `c_glycocalyx_recognition` | recall | Pass | Pass | Fail | Fail | Fail | Correct role is the longest and most detailed choice; other roles are unrelated. Rationale mainly repeats recognition. |
| 10 | `c_membrane_asymmetry` | recall | Pass | Pass | Fail | Fail | Fail | Only proteins and cholesterol layers are easy unsupported contrasts. The word compare does not make recognizing differ in composition a reasoning task. |

## Grounding evidence

- Q1: “They form a bilayer with heads toward water on both sides and tails tucked inside.”
- Q2: “Phospholipids have water-loving heads and water-avoiding tails: this combination is called amphipathic.”
- Q3: “Double bonds kink unsaturated tails, making close packing harder and increasing fluidity.”
- Q4: “Tail saturation, chain length, temperature, and cholesterol affect it.”
- Q5: “Cholesterol buffers fluidity: it restrains movement when the membrane is very fluid and prevents excessive stiffening in the cold.”
- Q6: “Cholesterol buffers fluidity: it restrains movement when the membrane is very fluid and prevents excessive stiffening in the cold.”
- Q7: “The fluid mosaic model describes a mixture of lipids, cholesterol, and proteins whose components can move sideways.”
- Q8: “Lipids rarely switch leaflets without enzymes such as flippases, floppases, and scramblases.”
- Q9: “Sugars on the outer surface form the glycocalyx and help cells recognize one another, including distinguishing self from non-self.”
- Q10: “The two membrane layers, or leaflets, differ in composition.”

## Priority revisions

1. Replace unrelated distractors with plausible confusions of the same taught relationship. Q8 is a clear example: all options should describe how enzyme help relates to switching, rather than offering unrelated molecules.

2. Redesign recall items as supported applications. Comparing two stated conditions may help, but avoid turning two equivalent effects into competing answers. Do not add mechanisms or predictions the short explanation never taught.

3. Reduce cues from option category, length, and grammar, especially Q7–Q9. Check that alternative wording tests a different target rather than disguising repetition.

4. Improve the rationale by explaining the tested relationship, not by adding unsupported detail. Q1–Q3 provide the clearest causal rationales in this sample.

## Shared limits

The current Tutor fixture is short and contains several list/definition facts, which limits the available reasoning targets. Do not force ten reasoning questions by inventing facts; assess the teaching material as well as the Assessment prompt when deciding the next design change.

The controller currently validates structure, references, and scoring evidence; it does not enforce this manual pedagogical review. The evaluation database saved the quiz as awaiting_answers before this review. Marking the report needs_revision does not add an application quality gate.

No paid call was made for this review. Structured-output success remains a useful format result, while pedagogical quality is not approved.


## Separate manual revision available

The original verdict and live evidence above are unchanged. See `revisions/2026-10-26-manual-quiz-revision.md` for rewritten questions, rationale for each change, and validation evidence. Its separate backend JSON includes private keys. This assistant-authored draft passed the existing validator and an offline real-controller persistence/privacy check, with no paid calls. It does not demonstrate that the current Assessment prompt generates the revised quality automatically.
