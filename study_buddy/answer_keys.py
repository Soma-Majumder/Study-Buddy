"""Private answer keys (A2).

Only controller code calls these functions. The rule they enforce: a correct
option or rationale is read only through `get_answer_key`, which the grader
uses after submission. Everything that goes to the learner or the Tutor comes
from `get_client_quiz`, which never touches the answer_keys table.
"""
import secrets
import sqlite3
import string

_ALPHABET = string.ascii_lowercase + string.digits


def new_id(prefix):
    return f"{prefix}_" + "".join(secrets.choice(_ALPHABET) for _ in range(12))


def save_quiz(conn, quiz_id, session_id, explanation_id, quiz_output):
    """Store a validated Assessment quiz output. Questions and keys go to separate tables.

    Returns {position: question_id}. Writes in one transaction, so a failed
    save leaves no quiz behind and the caller can record a failed or
    pending-save status.
    """
    keys = {k["position"]: k for k in quiz_output["answer_key"]}
    question_ids = {}
    with conn:
        conn.execute(
            "INSERT INTO quizzes (quiz_id, session_id, explanation_id, round) VALUES (?, ?, ?, ?)",
            (quiz_id, session_id, explanation_id, quiz_output["round"]),
        )
        for q in quiz_output["quiz"]["questions"]:
            qid = new_id("q")
            question_ids[q["position"]] = qid
            o = q["options"]
            conn.execute(
                "INSERT INTO questions (question_id, quiz_id, position, concept_id, prompt,"
                " option_a, option_b, option_c, option_d) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (qid, quiz_id, q["position"], q["concept_id"], q["prompt"], o["a"], o["b"], o["c"], o["d"]),
            )
            k = keys[q["position"]]
            conn.execute(
                "INSERT INTO answer_keys (question_id, correct_option, rationale) VALUES (?, ?, ?)",
                (qid, k["correct_option"], k["rationale"]),
            )
    return question_ids


def get_client_quiz(conn, quiz_id):
    """The quiz as the learner (or the Tutor) may see it: no keys, no rationales."""
    rows = conn.execute(
        "SELECT question_id, position, prompt, option_a, option_b, option_c, option_d"
        " FROM questions WHERE quiz_id = ? ORDER BY position",
        (quiz_id,),
    ).fetchall()
    return {
        "quiz_id": quiz_id,
        "questions": [
            {
                "question_id": r[0],
                "position": r[1],
                "prompt": r[2],
                "options": {"a": r[3], "b": r[4], "c": r[5], "d": r[6]},
            }
            for r in rows
        ],
    }


def get_answer_key(conn, quiz_id):
    """Controller-only. Used by grading after the learner submits."""
    rows = conn.execute(
        "SELECT k.question_id, k.correct_option, k.rationale, q.concept_id"
        " FROM answer_keys k JOIN questions q ON q.question_id = k.question_id"
        " WHERE q.quiz_id = ? ORDER BY q.position",
        (quiz_id,),
    ).fetchall()
    return [
        {"question_id": r[0], "correct_option": r[1], "rationale": r[2], "concept_id": r[3]}
        for r in rows
    ]
