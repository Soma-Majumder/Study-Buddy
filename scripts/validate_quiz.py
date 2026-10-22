"""Check an Assessment quiz output against its handoff.

Usage: validate_quiz.py <quiz_output.json> <tutor_to_assessment.json>

Schema checks need `pip install jsonschema`. The semantic rules below are the
ones the PRD cares about and a schema cannot express.
"""
import json, sys
from pathlib import Path

import jsonschema

SCHEMA = Path(__file__).resolve().parent.parent / "schemas/v1/quiz_output.schema.json"


def check(quiz_out, handoff):
    validator = jsonschema.Draft202012Validator(json.loads(SCHEMA.read_text()))
    errors = [e.message for e in validator.iter_errors(quiz_out)]
    if errors:
        return errors

    payload = handoff["payload"]
    if quiz_out["handoff_id"] != handoff["handoff_id"]:
        errors.append("handoff_id does not match the Tutor handoff")
    if quiz_out["explanation_id"] != payload["explanation_id"]:
        errors.append("explanation_id does not match the Tutor handoff")
    if quiz_out["round"] != handoff["context"]["round"]:
        errors.append("round does not match the Tutor handoff")

    taught = {c["concept_id"] for c in payload["concepts"]}
    allowed = set(payload.get("focus_concept_ids") or taught)
    questions = quiz_out["quiz"]["questions"]
    positions = [q["position"] for q in questions]
    if sorted(positions) != list(range(1, 11)):
        errors.append("positions must be exactly 1 to 10, each once")
    for q in questions:
        if q["concept_id"] not in taught:
            errors.append(f"q{q['position']}: concept {q['concept_id']} was not taught")
        elif q["concept_id"] not in allowed:
            errors.append(f"q{q['position']}: concept {q['concept_id']} is outside the retry focus")
        if len({o.strip().lower() for o in q["options"].values()}) != 4:
            errors.append(f"q{q['position']}: options must be four different answers")

    key_positions = [k["position"] for k in quiz_out["answer_key"]]
    if sorted(key_positions) != sorted(positions):
        errors.append("answer_key must have one entry for every question")
    return errors


if __name__ == "__main__":
    quiz_out = json.loads(Path(sys.argv[1]).read_text())
    handoff = json.loads(Path(sys.argv[2]).read_text())
    problems = check(quiz_out, handoff)
    for p in problems:
        print("FAIL:", p)
    print("valid" if not problems else f"{len(problems)} problem(s)")
    sys.exit(1 if problems else 0)
