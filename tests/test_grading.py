import copy
import json
import unittest
from pathlib import Path

from study_buddy.grading import grade
from study_buddy.result_check import check

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "schemas/v1/examples"
QUIZ = json.loads((EX / "quiz_output.example.json").read_text())
EXPLANATION = json.loads((EX / "result_explanation.example.json").read_text())
RETURN_HANDOFF = json.loads((EX / "assessment_to_tutor.example.json").read_text())

KEY = [
    {
        "question_id": f"q_{i:012d}",
        "correct_option": k["correct_option"],
        "rationale": k["rationale"],
        "concept_id": q["concept_id"],
    }
    for i, (k, q) in enumerate(zip(QUIZ["answer_key"], QUIZ["quiz"]["questions"]), start=1)
]


def answers_missing(positions):
    """All correct except the given 1-based positions, which get a wrong option."""
    out = {}
    for i, k in enumerate(KEY, start=1):
        right = k["correct_option"]
        out[k["question_id"]] = ("a" if right != "a" else "b") if i in positions else right
    return out


class GradingTests(unittest.TestCase):
    def test_perfect_score_has_no_weak_concepts(self):
        g = grade(KEY, answers_missing(set()))
        self.assertEqual((g["score"], g["passed"], g["weak_concepts"]), (10, True, []))

    def test_eight_passes(self):
        g = grade(KEY, answers_missing({1, 2}))
        self.assertEqual((g["score"], g["passed"]), (8, True))

    def test_seven_does_not_pass(self):
        g = grade(KEY, answers_missing({1, 2, 3}))
        self.assertEqual((g["score"], g["passed"]), (7, False))

    def test_six_of_ten_matches_the_return_handoff_example(self):
        g = grade(KEY, answers_missing({3, 4, 6, 7}))
        self.assertEqual(g["score"], RETURN_HANDOFF["payload"]["score"])
        self.assertEqual(
            {w["concept_id"]: w["missed_count"] for w in g["weak_concepts"]},
            {w["concept_id"]: w["missed_count"] for w in RETURN_HANDOFF["payload"]["weak_concepts"]},
        )

    def test_unanswered_question_is_an_error_not_a_wrong_answer(self):
        a = answers_missing(set())
        a.pop(KEY[0]["question_id"])
        with self.assertRaises(ValueError):
            grade(KEY, a)

    def test_answer_for_unknown_question_is_rejected(self):
        a = answers_missing(set())
        a["q_unknown"] = "a"
        with self.assertRaises(ValueError):
            grade(KEY, a)


class ResultCheckTests(unittest.TestCase):
    def setUp(self):
        self.graded = grade(KEY, answers_missing({3, 4, 6, 7}))

    def test_sample_explanation_agrees_with_grading(self):
        self.assertEqual(check(EXPLANATION, self.graded), [])

    def test_sample_explanation_matches_schema(self):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema = json.loads((ROOT / "schemas/v1/result_explanation.schema.json").read_text())
        jsonschema.validate(EXPLANATION, schema)

    def test_invented_weak_concept_is_rejected(self):
        e = copy.deepcopy(EXPLANATION)
        e["weak_concepts"].append({"concept_id": "c_lipid_rafts", "missed_count": 1})
        self.assertTrue(any("not in this quiz" in m for m in check(e, self.graded)))

    def test_hidden_weak_concept_is_rejected(self):
        e = copy.deepcopy(EXPLANATION)
        e["weak_concepts"].pop()
        self.assertTrue(any("missing weak concept" in m for m in check(e, self.graded)))

    def test_wrong_missed_count_is_rejected(self):
        e = copy.deepcopy(EXPLANATION)
        e["weak_concepts"][0]["missed_count"] = 1
        self.assertTrue(any("missed_count" in m for m in check(e, self.graded)))

    def test_correct_concept_flagged_weak_is_rejected(self):
        e = copy.deepcopy(EXPLANATION)
        e["weak_concepts"].append({"concept_id": "c_fluid_mosaic_model", "missed_count": 1})
        self.assertTrue(any("not a weak concept" in m for m in check(e, self.graded)))

    def test_missing_feedback_entry_is_rejected(self):
        e = copy.deepcopy(EXPLANATION)
        e["feedback"].pop()
        self.assertTrue(any("one entry per question" in m for m in check(e, self.graded)))


if __name__ == "__main__":
    unittest.main()
