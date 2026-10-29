import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from study_buddy.lessons import load_lessons

ROOT = Path(__file__).resolve().parent.parent
LESSON = json.loads((ROOT / "lessons/cell-membranes.json").read_text())


def fresh_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript((ROOT / "db/schema.sql").read_text())
    return conn


class LessonLoadTests(unittest.TestCase):
    def test_loads_every_concept_with_its_passage(self):
        conn = fresh_db()
        self.assertIn(("lesson_cell_membranes", LESSON["version"]), load_lessons(conn))
        rows = conn.execute("SELECT concept_id, source_passage FROM concepts WHERE lesson_id='lesson_cell_membranes'").fetchall()
        self.assertEqual({r[0] for r in rows}, {c["concept_id"] for c in LESSON["concepts"]})
        for concept_id, passage in rows:
            self.assertEqual(passage, LESSON["source_passages"][concept_id])

    def test_lesson_row_holds_everything_the_tutor_reads(self):
        conn = fresh_db()
        load_lessons(conn)
        row = conn.execute(
            "SELECT source_text, attribution, adaptation_notice, approval, approved FROM lessons WHERE lesson_id='lesson_cell_membranes'"
        ).fetchone()
        self.assertEqual(row[0], LESSON["source_text"])
        self.assertEqual(row[1], f"{LESSON['book']}, {LESSON['authors']}")
        self.assertIn("Adapted", row[2])
        self.assertEqual(json.loads(row[3]), LESSON["source_text_approval"])
        self.assertEqual(row[4], 1)

    def test_loading_twice_does_not_duplicate(self):
        conn = fresh_db()
        load_lessons(conn)
        load_lessons(conn)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0], 4)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM concepts WHERE lesson_id='lesson_cell_membranes'").fetchone()[0], len(LESSON["concepts"]))

    def test_unapproved_lesson_is_not_loaded(self):
        with tempfile.TemporaryDirectory() as d:
            draft = dict(LESSON, approved=False)
            (Path(d) / "draft.json").write_text(json.dumps(draft))
            conn = fresh_db()
            self.assertEqual(load_lessons(conn, d), [])
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM lessons").fetchone()[0], 0)

    def test_lesson_file_has_no_stale_review_notes(self):
        self.assertNotIn("open_items", LESSON)
        self.assertTrue(LESSON["adaptation_notice"].startswith("Adapted"))


if __name__ == "__main__":
    unittest.main()
