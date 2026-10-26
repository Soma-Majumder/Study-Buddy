# Reference-guided Assessment evaluation

Run: `results/2026-10-26T19-22-35-809046+00-00-assessment-controller.json`. Compared with the pre-reference schema-valid run at 19:10 using the same hand-written Tutor explanation, low effort, 8192-token cap, 60/70-second deadlines, structured response schema, and controller limits. This is one sample per prompt, not a controlled reliability study. Prompt and quality-reference hashes are recorded.

## Outcome: format passes; question quality needs revision

One generation, no repair, 26,821 ms. Controller saved one quiz, ten questions, and ten private keys; status awaiting_answers. No attempts/results or learning retry, no key leak or foreign-key error. These are format/persistence checks, not a semantic quality gate.

Usage: 5637 input tokens, 6260 output tokens, including 4299 reported thinking tokens (68.7%). Previous schema-valid run used 5213 input and 6284 output tokens in 27,256 ms. These two samples do not establish a speed or token improvement.

## Per-question assistant review

| Q | Primary demand | Finding |
| --- | --- | --- |
| 1 | Recall arrangement | Correct answer matches the explanation, but rationale calls heads amphipathic. The explanation uses amphipathic for the whole water-loving-head/water-avoiding-tail combination. |
| 2 | Simple cause/effect | Correct relationship, but the stem already says water-avoiding and the answer says avoid water. Two wrong answers are near duplicates, while protein binding is unrelated. |
| 3 | Cause/effect | Supported keyed packing/fluidity relationship. Wrong choice d says removing ability to pack increases packing: internally contradictory and easy to reject. |
| 4 | Comparison | Correct packing comparison, but overlaps Q3. One distractor calls tails amphipathic; the supplied explanation describes the tails as water-avoiding, with amphipathic referring to the combined molecule. |
| 5 | Recall list | Memorized factor list; distractor lists mix unrelated processes and have uneven specificity. |
| 6 | Recall model | Correct definition, but it is the only full detailed moving-mixture description; other choices describe unrelated structures. |
| 7 | Recall conditional role | Correct restraint role. Double-bond and sugar-removal choices remain weak rather than plausible alternative effects on movement. |
| 8 | Recall conditional role | Reject ambiguity: increasing movement and preventing excessive cold stiffness can both appear correct. Key c does not resolve that overlap. |
| 9 | Recall enzyme names | Correct choice is the only enzyme list; the prior specialist-name/category-cue problem remains. |
| 10 | Recall role | Correct recognition role, with unrelated wrong roles and a longer correct choice. |

Rationales are one sentence, 11–17 words. Keys mostly correspond to explicitly taught facts, but a keyed answer alone is insufficient: overlapping alternatives and an inaccurate rationale block approval.

## Comparison with the earlier sample

The earlier schema-valid quiz had two clear understanding items; this sample has roughly three (Q2–Q4), with Q2 shallow and Q3/Q4 overlapping in target. That does not establish a meaningful improvement or meet the six-item review target. The manually revised quiz's parallel distractor design, application of definitions, qualifier preservation, and evidence-boundary patterns were not consistently transferred. No manual question stem was supplied to the model, so verbatim example copying was avoided by construction; that does not make the new content strong.

The reference-guide prompt alone has not solved the pedagogical problem. The next design decision should address explicit semantic review and revision with a separately defined bounded budget, rather than keep adding guidance or confuse grammar constraints with factual/educational validation. No such stage was silently added in this evaluation. No further paid call was made.
