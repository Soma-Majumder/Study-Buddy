"""Offline contract test: real JS Tutor harness -> Python Assessment helpers.

Claude's response and Assessment's quiz are fixtures, not AI generations.
Requires Node 22+, installed backend packages, and requirements-dev.txt.
"""
import copy
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import unittest

from scripts.validate_quiz import check
from study_buddy.answer_keys import get_answer_key, get_client_quiz, save_quiz
from study_buddy.grading import grade

ROOT = Path(__file__).resolve().parent.parent


class TutorAssessmentIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        node = os.environ.get("STUDY_BUDDY_NODE") or shutil.which("node")
        if not node:
            raise RuntimeError("Node 22+ is required; put node on PATH or set STUDY_BUDDY_NODE.")
        # Exercise input preparation, provider response parsing, and output
        # validation together. The supplied fetch function never uses a network.
        script = r"""
import {readFile} from 'node:fs/promises';
import {runTutor} from './backend/tutor.mjs';
const fixture = JSON.parse(await readFile('schemas/v1/examples/tutor_to_assessment.example.json', 'utf8'));
const context = {lessonId: 'lesson_cell_membranes',
  sessionId: 'session_q8w1e5r2t6y4', handoffId: 'handoff_a1b2c3d4e5f6',
  explanationId: 'expl_z9y8x7w6v5u4'};
let calls = 0;
const result = await runTutor(context, {apiKey: 'fake-integration-key', fetchImpl: async (_url, options) => {
  calls++;
  const input = JSON.parse(JSON.parse(options.body).messages[0].content);
  fixture.handoff_id = input.controller_context.handoff_id;
  fixture.context = {learner_session_id: input.controller_context.learner_session_id,
    round: input.controller_context.round};
  fixture.payload.explanation_id = input.controller_context.explanation_id;
  fixture.sources = input.sources;
  fixture.expected_output = input.expected_output;
  return {ok: true, json: async () => ({stop_reason: 'end_turn',
    content: [{type: 'text', text: JSON.stringify(fixture)}], model: 'offline-fixture',
    usage: {input_tokens: 10, output_tokens: 5}})};
}});
console.log(JSON.stringify({handoff: result.handoff, calls}));
"""
        completed = subprocess.run(
            [node, "--input-type=module", "-e", script], cwd=ROOT,
            capture_output=True, text=True, check=True, timeout=15,
        )
        result = json.loads(completed.stdout)
        if result["calls"] != 1:
            raise AssertionError("Valid fixture should need exactly one fake provider call.")
        cls.handoff = result["handoff"]

    def setUp(self):
        self.quiz = json.loads((ROOT / "schemas/v1/examples/quiz_output.example.json").read_text())
        # Stand in for Assessment copying IDs from the handoff it receives.
        self.quiz["handoff_id"] = self.handoff["handoff_id"]
        self.quiz["explanation_id"] = self.handoff["payload"]["explanation_id"]
        self.quiz["round"] = self.handoff["context"]["round"]

    def test_valid_handoff_quiz_and_storage_keep_keys_private(self):
        self.assertEqual(check(self.quiz, self.handoff), [])
        lesson = json.loads((ROOT / "lessons/cell-membranes.json").read_text())
        p = self.handoff["payload"]
        session = self.handoff["context"]["learner_session_id"]
        with sqlite3.connect(":memory:") as conn:
            conn.executescript((ROOT / "db/schema.sql").read_text())
            conn.execute("INSERT INTO lessons VALUES (?, ?, ?, ?, ?, ?, ?, 1)", (
                lesson["lesson_id"], lesson["version"], lesson["title"], lesson["source_text"],
                lesson["source_url"], lesson["license"], self.handoff["sources"][0]["attribution"],
            ))
            for c in lesson["concepts"]:
                conn.execute("INSERT INTO concepts VALUES (?, ?, ?, ?, ?)", (
                    c["concept_id"], lesson["lesson_id"], lesson["version"], c["name"], c["summary"],
                ))
            conn.execute("INSERT INTO learners VALUES ('learner_integration')")
            conn.execute("INSERT INTO study_sessions VALUES (?, 'learner_integration', ?, ?, 'teaching', NULL, 0, 'test')",
                         (session, p["lesson_id"], p["lesson_version"]))
            conn.execute("INSERT INTO explanations VALUES (?, ?, 'first', ?)",
                         (p["explanation_id"], session, p["explanation_text"]))
            for c in p["concepts"]:
                conn.execute("INSERT INTO explanation_concepts VALUES (?, ?)",
                             (p["explanation_id"], c["concept_id"]))
            save_quiz(conn, "quiz_integration1", session, p["explanation_id"], self.quiz)
            client = get_client_quiz(conn, "quiz_integration1")
            keys = get_answer_key(conn, "quiz_integration1")
            self.assertEqual(len(client["questions"]), 10)
            self.assertEqual(len(keys), 10)
            self.assertEqual([k["correct_option"] for k in keys],
                             [k["correct_option"] for k in self.quiz["answer_key"]])
            for q in client["questions"]:
                self.assertEqual(set(q), {"question_id", "position", "prompt", "options"})
            for key in self.quiz["answer_key"]:
                self.assertNotIn(key["rationale"], json.dumps(client))
            self.assertEqual(conn.execute("PRAGMA foreign_key_check").fetchall(), [])

    def test_stale_ids_and_round_are_rejected_across_the_boundary(self):
        for field, value in [("handoff_id", "handoff_stale"),
                             ("explanation_id", "expl_stale"), ("round", "retry")]:
            with self.subTest(field=field):
                invalid = copy.deepcopy(self.quiz)
                invalid[field] = value
                self.assertTrue(any(f"{field} does not match" in e for e in check(invalid, self.handoff)))

    def test_wrong_quiz_size_and_untaught_concepts_are_rejected(self):
        short = copy.deepcopy(self.quiz)
        short["quiz"]["questions"].pop()
        self.assertTrue(check(short, self.handoff))
        untaught = copy.deepcopy(self.quiz)
        untaught["quiz"]["questions"][0]["concept_id"] = "c_not_taught"
        self.assertTrue(any("was not taught" in e for e in check(untaught, self.handoff)))


    def retry_context_from_grade(self, missed_positions):
        # These keys stand in for the controller's private DB read. The real
        # grader computes score and missed counts; the test never invents them.
        keys = [dict(k, question_id=f"q_{i:012d}", concept_id=q["concept_id"])
                for i, (k, q) in enumerate(zip(self.quiz["answer_key"], self.quiz["quiz"]["questions"]), 1)]
        answers = {k["question_id"]: (
            ("b" if k["correct_option"] == "a" else "a") if i in missed_positions
            else k["correct_option"])
            for i, k in enumerate(keys, 1)}
        graded = grade(keys, answers)
        back = json.loads((ROOT / "schemas/v1/examples/assessment_to_tutor.example.json").read_text())
        p = self.handoff["payload"]
        session = self.handoff["context"]["learner_session_id"]
        back["context"]["learner_session_id"] = session
        back["sources"] = copy.deepcopy(self.handoff["sources"])
        back["payload"].update(lesson_id=p["lesson_id"], lesson_version=p["lesson_version"],
                               score=graded["score"], weak_concepts=graded["weak_concepts"])
        return {
            "lessonId": p["lesson_id"], "sessionId": session,
            "handoffId": "handoff_b1b2c3d4e5f6", "explanationId": "expl_a9y8x7w6v5u4",
            "round": "retry", "learningRetryCount": 0, "returnHandoff": back,
            "currentAttempt": {"sessionId": session, "lessonId": p["lesson_id"],
                "lessonVersion": p["lesson_version"], "round": "first",
                "quizId": back["payload"]["quiz_id"], "attemptId": back["payload"]["attempt_id"],
                "score": graded["score"],
                "missedConceptCounts": {c["concept_id"]: c["missed_count"] for c in graded["weak_concepts"]}},
        }

    def prepare_reteaching(self, context):
        script = r"""
import {buildTutorInput} from './backend/tutor-input.mjs';
let data = '';
for await (const chunk of process.stdin) data += chunk;
try {
  const request = await buildTutorInput(JSON.parse(data));
  console.log(JSON.stringify({accepted: true, input: JSON.parse(request.user)}));
} catch {
  console.log(JSON.stringify({accepted: false}));
}
"""
        completed = subprocess.run(
            [os.environ.get("STUDY_BUDDY_NODE") or shutil.which("node"), "--input-type=module", "-e", script],
            cwd=ROOT, input=json.dumps(context), capture_output=True, text=True, check=True, timeout=15,
        )
        return json.loads(completed.stdout)

    def test_controller_six_of_ten_prepares_only_the_actual_missed_concepts(self):
        context = self.retry_context_from_grade({3, 4, 6, 7})
        self.assertEqual(context["currentAttempt"]["score"], 6)
        result = self.prepare_reteaching(context)
        self.assertTrue(result["accepted"])
        expected = {"c_fatty_acid_saturation", "c_cholesterol_buffer"}
        self.assertEqual(set(result["input"]["focus_concept_ids"]), expected)
        self.assertEqual({c["concept_id"] for c in result["input"]["approved_lesson"]["concepts"]}, expected)
        self.assertNotIn("glycocalyx", result["input"]["approved_lesson"]["source_text"])
        self.assertNotIn("answer_key", result["input"])
        self.assertNotIn("correct_option", json.dumps(result["input"]))

    def test_controller_seven_allows_reteaching_but_eight_blocks_it(self):
        for missed, score, allowed in [({3, 4, 6}, 7, True), ({3, 4}, 8, False)]:
            with self.subTest(score=score):
                context = self.retry_context_from_grade(missed)
                self.assertEqual(context["currentAttempt"]["score"], score)
                self.assertEqual(self.prepare_reteaching(context)["accepted"], allowed)

    def test_missing_weak_concepts_or_spent_retry_blocks_return_handoff(self):
        for failure in ["missing_weak_concepts", "spent_retry"]:
            with self.subTest(failure=failure):
                context = self.retry_context_from_grade({3, 4, 6, 7})
                if failure == "missing_weak_concepts":
                    context["returnHandoff"]["payload"]["weak_concepts"] = []
                else:
                    context["learningRetryCount"] = 1
                self.assertFalse(self.prepare_reteaching(context)["accepted"])


if __name__ == "__main__":
    unittest.main()
