"""Check Assessment's result explanation against the controller's own grading."""


def check(explanation, graded):
    errors = []
    positions = [f["position"] for f in explanation["feedback"]]
    if sorted(positions) != [i["position"] for i in graded["items"]]:
        errors.append("feedback must have exactly one entry per question")

    expected = {w["concept_id"]: w["missed_count"] for w in graded["weak_concepts"]}
    got = {w["concept_id"]: w["missed_count"] for w in explanation["weak_concepts"]}
    quiz_concepts = {i["concept_id"] for i in graded["items"]}
    for c in got:
        if c not in quiz_concepts:
            errors.append(f"weak concept {c} is not in this quiz")
    for c in expected.keys() - got.keys():
        errors.append(f"missing weak concept {c}")
    for c in got.keys() - expected.keys():
        if c in quiz_concepts:
            errors.append(f"{c} was not missed, so it is not a weak concept")
    for c in expected.keys() & got.keys():
        if expected[c] != got[c]:
            errors.append(f"{c}: missed_count {got[c]} does not match graded {expected[c]}")
    return errors
