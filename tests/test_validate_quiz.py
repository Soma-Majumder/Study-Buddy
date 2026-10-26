import copy
import json
import unittest
from pathlib import Path

from scripts.validate_quiz import check

EXAMPLES = Path(__file__).resolve().parent.parent / "schemas/v1/examples"


def load(name):
    return json.loads((EXAMPLES / name).read_text())


class ValidateQuizTests(unittest.TestCase):
    def setUp(self):
        self.quiz = load("quiz_output.example.json")
        self.handoff = load("tutor_to_assessment.example.json")

    def problems(self):
        return check(self.quiz, self.handoff)

    def assertProblem(self, text):
        found = self.problems()
        self.assertTrue(any(text in p for p in found), f"{text!r} not in {found}")

    def test_example_quiz_is_valid(self):
        self.assertEqual(self.problems(), [])

    def test_exactly_ten_questions_required(self):
        del self.quiz["quiz"]["questions"][-1]
        self.assertTrue(self.problems())
        self.quiz = load("quiz_output.example.json")
        self.quiz["quiz"]["questions"].append(copy.deepcopy(self.quiz["quiz"]["questions"][0]))
        self.assertTrue(self.problems())

    def test_answer_key_must_have_ten_entries(self):
        del self.quiz["answer_key"][-1]
        self.assertTrue(self.problems())

    def test_correct_option_must_be_a_to_d(self):
        self.quiz["answer_key"][0]["correct_option"] = "e"
        self.assertTrue(self.problems())

    def test_options_must_be_exactly_a_to_d(self):
        del self.quiz["quiz"]["questions"][0]["options"]["d"]
        self.assertTrue(self.problems())
        self.quiz = load("quiz_output.example.json")
        self.quiz["quiz"]["questions"][0]["options"]["e"] = "extra"
        self.assertTrue(self.problems())

    def test_rationale_required(self):
        del self.quiz["answer_key"][0]["rationale"]
        self.assertTrue(self.problems())

    def test_missing_concept_id_fails(self):
        del self.quiz["quiz"]["questions"][0]["concept_id"]
        self.assertTrue(self.problems())

    def test_concept_not_taught_fails(self):
        self.quiz["quiz"]["questions"][0]["concept_id"] = "c_not_in_handoff"
        self.assertProblem("was not taught")

    def test_duplicate_positions_fail(self):
        self.quiz["quiz"]["questions"][1]["position"] = 1
        self.assertProblem("positions must be exactly 1 to 10")

    def test_answer_key_must_match_question_positions(self):
        self.quiz["answer_key"][0]["position"] = self.quiz["answer_key"][1]["position"]
        self.assertProblem("answer_key must have one entry")

    def test_duplicate_options_fail_ignoring_case_and_whitespace(self):
        options = self.quiz["quiz"]["questions"][0]["options"]
        options["b"] = "  " + options["a"].upper() + " "
        self.assertProblem("four different answers")

    def test_handoff_id_mismatch_fails(self):
        self.quiz["handoff_id"] = "handoff_9999"
        self.assertProblem("handoff_id")

    def test_explanation_id_mismatch_fails(self):
        self.quiz["explanation_id"] = "expl_9999"
        self.assertProblem("explanation_id")

    def test_round_mismatch_fails(self):
        self.quiz["round"] = "retry"
        self.assertProblem("round")

    def test_unknown_top_level_field_fails(self):
        self.quiz["extra"] = True
        self.assertTrue(self.problems())

    def test_retry_quiz_must_stay_in_focus(self):
        self.handoff = load("tutor_to_assessment.retry.example.json")
        self.quiz["handoff_id"] = self.handoff["handoff_id"]
        self.quiz["explanation_id"] = self.handoff["payload"]["explanation_id"]
        self.quiz["round"] = "retry"
        focus = self.handoff["payload"]["focus_concept_ids"]
        for i, q in enumerate(self.quiz["quiz"]["questions"]):
            q["concept_id"] = focus[i % 2]
        self.assertEqual(self.problems(), [])
        self.quiz["quiz"]["questions"][0]["concept_id"] = "c_fluid_mosaic_model"
        self.assertProblem("was not taught")

    def test_retry_concept_taught_but_outside_focus_fails(self):
        self.handoff = load("tutor_to_assessment.retry.example.json")
        self.handoff["payload"]["concepts"].append(
            {"concept_id": "c_fluid_mosaic_model", "name": "n", "taught_summary": "s"}
        )
        self.quiz["handoff_id"] = self.handoff["handoff_id"]
        self.quiz["explanation_id"] = self.handoff["payload"]["explanation_id"]
        self.quiz["round"] = "retry"
        for q in self.quiz["quiz"]["questions"]:
            q["concept_id"] = "c_cholesterol_buffer"
        self.quiz["quiz"]["questions"][0]["concept_id"] = "c_fluid_mosaic_model"
        self.assertProblem("outside the retry focus")


if __name__ == "__main__":
    unittest.main()
