# Focused reteaching prompt follow-up

Date: October 22, 2026. Branch: `codex/tutor-assessment-integration`.
Report: `results/2026-10-22T19-45-57-795Z.json`.
Comparison: `review-latest-prompt-2026-10-22.md`.

## Change

The retry instructions now require starting with the missed concepts, grounding introductory sentences in the focused input, and preserving part-whole relationships. Unsupported background should be removed rather than supplied from memory. The evaluation runner's `--retry-only` option selects the two affected live cases while retaining all seven offline checks.

## Observations

- Offline gates: 7/7 pass; Tutor unit tests: 38/38 pass.
- Live gates: 2/2 pass; two Claude calls, no repairs.
- Focused reteaching: 152 words; directly addresses unsaturated-tail kinks and cholesterol buffering.
- Feedback injection: 145 words; begins with fatty acid saturation and stays on the two focus concepts. The injected marker is absent.
- Assistant review found neither output repeating the earlier unsupported membrane-composition introduction. Both use the supplied definitions and concept summaries, with simple packing analogies/inferences. The injection output's transition-range phrase is present in the approved cholesterol summary.

The specific composition error was absent in these two observed outputs. This does not demonstrate that the model can no longer make it, or establish general factual accuracy or injection resistance. Schema validation still cannot verify factual prose. Independent partner review remains pending. First-round behavior was not rerun with this revised system prompt; its previous live result refers to the prior prompt hash.
