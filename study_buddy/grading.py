"""Deterministic grading and weak-concept detection (A3).

Code, not a model, decides the score and which concepts were missed. Assessment
(Agent 2) only writes the explanations; `result_check.check` makes sure its
output agrees with what this module computed.
"""

PASS_THRESHOLD = 8  # PRD: pass at 8/10


def grade(answer_key, answers):
    """Grade one attempt.

    answer_key: list from answer_keys.get_answer_key (ordered by position)
    answers:    {question_id: 'a'|'b'|'c'|'d'}

    Every question must be answered. An unanswered question is not graded as
    wrong, because the controller must ask the learner first (PRD: technical
    failures never count as wrong answers).
    """
    missing = [k["question_id"] for k in answer_key if k["question_id"] not in answers]
    if missing:
        raise ValueError(f"unanswered questions: {missing}")
    unknown = set(answers) - {k["question_id"] for k in answer_key}
    if unknown:
        raise ValueError(f"answers for questions not in this quiz: {sorted(unknown)}")

    items = []
    missed = {}
    for position, k in enumerate(answer_key, start=1):
        selected = answers[k["question_id"]]
        correct = selected == k["correct_option"]
        items.append(
            {
                "position": position,
                "question_id": k["question_id"],
                "concept_id": k["concept_id"],
                "selected": selected,
                "correct_option": k["correct_option"],
                "is_correct": correct,
                "rationale": k["rationale"],
            }
        )
        if not correct:
            missed[k["concept_id"]] = missed.get(k["concept_id"], 0) + 1

    score = sum(i["is_correct"] for i in items)
    return {
        "score": score,
        "passed": score >= PASS_THRESHOLD,
        "items": items,
        # A concept is weak if the learner missed it at least once.
        "weak_concepts": [
            {"concept_id": c, "missed_count": n}
            for c, n in sorted(missed.items(), key=lambda cn: (-cn[1], cn[0]))
        ],
    }
