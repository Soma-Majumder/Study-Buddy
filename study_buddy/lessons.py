"""Load approved lesson files into the lessons and concepts tables.

lessons/*.json is the source of truth. Loading is idempotent, so the database
can be rebuilt from the files at any time. Unapproved lessons are skipped.
"""
import json
from pathlib import Path

LESSON_DIR = Path(__file__).resolve().parent.parent / "lessons"


def load_lessons(conn, directory=LESSON_DIR):
    """Insert approved lessons and their concepts. Returns [(lesson_id, version)] loaded."""
    loaded = []
    with conn:
        for path in sorted(Path(directory).glob("*.json")):
            lesson = json.loads(path.read_text())
            if lesson.get("approved") is not True:
                continue
            key = (lesson["lesson_id"], lesson["version"])
            conn.execute(
                "INSERT OR REPLACE INTO lessons (lesson_id, version, topic, source_text, source_url,"
                " license, attribution, approved, book, authors, adaptation_notice, approval)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (
                    *key,
                    lesson["title"],
                    lesson["source_text"],
                    lesson["source_url"],
                    lesson["license"],
                    f"{lesson['book']}, {lesson['authors']}",  # same string the Tutor sends as attribution
                    lesson["book"],
                    lesson["authors"],
                    lesson.get("adaptation_notice", ""),
                    json.dumps(lesson.get("source_text_approval")),
                ),
            )
            passages = lesson.get("source_passages", {})
            for c in lesson["concepts"]:
                conn.execute(
                    "INSERT OR REPLACE INTO concepts (concept_id, lesson_id, lesson_version, name, summary,"
                    " source_passage) VALUES (?, ?, ?, ?, ?, ?)",
                    (c["concept_id"], *key, c["name"], c["summary"], passages.get(c["concept_id"], "")),
                )
            loaded.append(key)
    return loaded
