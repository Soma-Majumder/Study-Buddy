# Assessment question-quality rubric

Use after schema validation, for both first-round and focused retry quizzes. Review only against the actual Tutor explanation supplied in that run. This is a manual review rubric, not a runtime validator, calibrated score, or PRD change. A schema pass alone is insufficient.

## Per question

Record the question position, concept ID, supporting sentence, and pass/fail with a short reason for each criterion:

| Criterion | Pass condition |
| --- | --- |
| Grounding and correctness | Correct answer and rationale follow from explicit teaching; a learner needs no unseen source or outside facts. |
| One defensible answer | Exactly one option answers the specific stem; no overlapping options or unstated assumptions. |
| Plausible distractors | Wrong options reflect a confusion of taught relationships, rather than absurd claims or category mismatches. The explanation lets the learner reject each one. |
| Clarity and fair cues | Short natural stem, parallel options, similar detail, no answer revealed by length, grammar, or a unique qualifier. |
| Useful reasoning | Rationale explains the relevant relationship rather than simply repeating the option. |

Also label the item's primary demand: recall, comparison, cause/effect, direct prediction, or misconception correction. Classify what the learner actually has to do, not the presence of words like “why.”

## Whole quiz

- Exactly ten valid questions, focused only on taught concepts. Retry scope remains unchanged.
- Aim for at least six understanding items (comparison, cause/effect, direct prediction, or correction), only where the teaching supports them. Do not invent content to meet this target.
- Repeated concepts should test distinct ideas. Record near duplicates explicitly.
- Check concept coverage against what the explanation can support; no arbitrary demand for equal counts.
- Check correct-option distribution for patterns, while keeping correctness and clarity more important than exact balance.
- Any grounding or answer-ambiguity failure blocks a learner-ready recommendation. Other failed criteria require revision before calling the quiz quality-approved. A single successful quiz is not a reliability estimate.

## Improvements motivated by the October 26 sample

| Observed issue | Better direction |
| --- | --- |
| “Which proteins are named as enzymes?” with sugars and cholesterol as wrong options | Ask how often lipids change leaflets without enzyme help. Keep all options as descriptions of switching frequency or its relationship to enzyme help. Do not ask for the molecular mechanism: that was not taught. |
| Correct answer is the only detailed description | Express all options at a similar level of detail; avoid one long supported explanation among three obviously unrelated nouns. |
| Multiple items repeat bilayer arrangement | Use one arrangement item, then a different taught relationship such as how tail shape affects packing. Keep concept mapping aligned with the actual target. |
| Mostly copying a sentence | Ask a direct consequence of that sentence when explicitly taught, such as comparing packing of bent and straight tails. Do not add new temperatures, numerical predictions, or chemistry. |

## Worked revision of the weak enzyme-name item

Supporting text: “Lipids rarely switch leaflets without enzymes such as flippases, floppases, and scramblases.”

Stem: A student says lipids often move from one membrane leaflet to the other without help. Which correction matches the explanation?

- a: Switching leaflets is common without enzyme help.
- b: Switching leaflets is rare without enzyme help.
- c: Switching leaflets is equally common with or without enzyme help.
- d: Enzyme help prevents lipids from switching leaflets.

Key: b. Rationale: The explanation describes switching as rare without the named enzymes; it does not describe frequent unaided switching. This tests the relationship between switching and enzyme help instead of recalling enzyme names. The rejected choices describe contrasting claims about that same relationship. This authored example is a review aid, not a live model result or a validated complete quiz. It does not establish the exact rate with enzyme help.

## Next evaluation

Run the updated prompt through the controller, then complete this rubric for every accepted question. Compare with the saved October 26 sample and report unresolved flaws. No live generation has yet verified the new quality instructions.
