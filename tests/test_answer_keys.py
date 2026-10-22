import json
import sqlite3
import unittest
from pathlib import Path

from study_buddy.answer_keys import get_answer_key, get_client_quiz, new_id, save_quiz

ROOT = Path(__file__).resolve().parent.parent
QUIZ = json.loads((ROOT / "schemas/v1/examples/quiz_output.example.json").read_text())


def fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "db/schema.sql").read_text())
    conn.execute(
        "INSERT INTO lessons (lesson_id, version, topic, source_text, source_url, license, attribution, approved)"
        " VALUES ('lesson_cell_membranes', 1, 't', 's', 'u', 'l', 'a', 1)"
    )
    for c in {q["concept_id"] for q in QUIZ["quiz"]["questions"]}:
        conn.execute(
            "INSERT INTO concepts (concept_id, lesson_id, lesson_version, name, summary)"
            " VALUES (?, 'lesson_cell_membranes', 1, 'n', 's')",
            (c,),
        )
    conn.execute("INSERT INTO learners VALUES ('learner_a')")
    conn.execute(
        "INSERT INTO study_sessions VALUES ('session_a', 'learner_a', 'lesson_cell_membranes', 1,"
        " 'started', NULL, 0, 'now')"
    )
    conn.execute("INSERT INTO explanations VALUES ('expl_0001', 'session_a', 'first', 'text')")
    return conn


class AnswerKeyTests(unittest.TestCase):
    def setUp(self):
        self.conn = fresh_db()
        self.quiz_id = new_id("quiz")
        save_quiz(self.conn, self.quiz_id, "session_a", "expl_0001", QUIZ)

    def test_client_quiz_has_ten_questions_and_no_key_fields(self):
        client = get_client_quiz(self.conn, self.quiz_id)
        self.assertEqual(len(client["questions"]), 10)
        text = json.dumps(client)
        for forbidden in ("correct_option", "rationale", "answer_key"):
            self.assertNotIn(forbidden, text)
        for q in client["questions"]:
            self.assertEqual(set(q), {"question_id", "position", "prompt", "options"})

    def test_client_quiz_does_not_contain_any_rationale_text(self):
        text = json.dumps(get_client_quiz(self.conn, self.quiz_id))
        for k in QUIZ["answer_key"]:
            self.assertNotIn(k["rationale"], text)

    def test_controller_can_read_keys_after_submission(self):
        keys = get_answer_key(self.conn, self.quiz_id)
        self.assertEqual([k["correct_option"] for k in keys], [k["correct_option"] for k in QUIZ["answer_key"]])
        self.assertTrue(all(k["concept_id"] for k in keys))

    def test_questions_table_has_no_key_column(self):
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(questions)")}
        self.assertFalse(cols & {"correct_option", "rationale"})

    def test_failed_save_leaves_nothing_behind(self):
        bad = json.loads(json.dumps(QUIZ))
        bad["answer_key"][9]["correct_option"] = "z"  # violates the table's check on the last row
        qid = new_id("quiz")
        with self.assertRaises(sqlite3.IntegrityError):
            save_quiz(self.conn, qid, "session_a", "expl_0001", bad)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM quizzes WHERE quiz_id = ?", (qid,)).fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM questions WHERE quiz_id = ?", (qid,)).fetchone()[0], 0)

    def test_new_ids_match_the_agreed_format(self):
        import re
        self.assertRegex(new_id("quiz"), r"^quiz_[a-z0-9]{12}$")
        self.assertRegex(new_id("q"), r"^q_[a-z0-9]{12}$")


if __name__ == "__main__":
    unittest.main()
