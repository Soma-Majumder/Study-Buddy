"""Pure reviewer-contract validation. No model calls, writes, or publication."""
import hashlib
import json
from pathlib import Path

import jsonschema
from scripts.validate_quiz import check as check_quiz

ROOT = Path(__file__).resolve().parent.parent
VALIDATOR = jsonschema.Draft202012Validator(json.loads((ROOT / 'schemas/v1/quiz_quality_review.schema.json').read_text()))
CRITERIA = ('grounding', 'exactly_one_answer', 'plausible_distractors', 'fair_wording', 'rationale_quality')
UNDERSTANDING = {'comparison', 'cause_effect', 'prediction', 'misconception_correction'}
# Quality-style problems are warnings, never blocks: the quiz is still published and the
# warning is returned for monitoring. Wrong, ambiguous, ungrounded, cued or uncertain
# items remain hard blocks. Six understanding items is the generation goal.
UNDERSTANDING_GOAL = 6
SOFT_CODES = {'insufficient_understanding', 'repeated_target'}
# An answer cue only warns when no individual question criterion failed; otherwise it blocks.
CONDITIONAL_SOFT_CODES = {'answer_cue'}
# After the one allowed revision, distractor-quality and wording problems no longer stop publication:
# the revised quiz is published with these as warnings. Content-correctness findings still block.
FINAL_REVIEW_SOFT_CODES = {'weak_distractor', 'answer_cue'}
FINAL_REVIEW_SOFT_CRITERIA = {'plausible_distractors', 'fair_wording'}


class ReviewRejected(ValueError):
    """Malformed, stale, or internally inconsistent reviewer response."""


def candidate_hash(candidate):
    """Bind the full private candidate, including its keys, to a versioned hash."""
    payload = json.dumps(candidate, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False)
    return hashlib.sha256(('study-buddy-private-quiz/v1\n' + payload).encode('utf-8')).hexdigest()


def validate_review(report, context, *, minimum_understanding=UNDERSTANDING_GOAL, final_revision=False):
    """Return pass/needs_revision/blocked, never permission to publish.

    context is backend-owned: operation_id, candidate_version, handoff, and
    candidate. The caller must authenticate ownership and load its snapshot.
    Fewer than UNDERSTANDING_GOAL understanding items and repeated targets are soft: they
    appear in `warnings` and never change the decision, as does an answer cue when no
    question criterion failed. Not a PRD change. With final_revision (the review of the single
    permitted revision), distractor-quality and wording failures also only warn.
    """
    if type(minimum_understanding) is not int or not 0 <= minimum_understanding <= 10:
        raise ValueError('Understanding target must be an integer from 0 to 10.')
    if list(VALIDATOR.iter_errors(report)):
        raise ReviewRejected('Reviewer report does not match the v1 contract.')
    handoff, candidate = context['handoff'], context['candidate']
    if check_quiz(candidate, handoff):
        raise ReviewRejected('Review requires a structurally valid candidate for this handoff.')
    expected = dict(operation_id=context['operation_id'], candidate_version=context['candidate_version'],
                    session_id=handoff['context']['learner_session_id'], lesson_id=handoff['payload']['lesson_id'],
                    lesson_version=handoff['payload']['lesson_version'], handoff_id=handoff['handoff_id'],
                    explanation_id=handoff['payload']['explanation_id'], round=handoff['context']['round'],
                    candidate_hash=candidate_hash(candidate))
    if any(report[field] != value for field, value in expected.items()):
        raise ReviewRejected('Reviewer report belongs to a different operation or candidate snapshot.')
    positions = [q['position'] for q in report['questions']]
    if sorted(positions) != list(range(1, 11)):
        raise ReviewRejected('Reviewer must cover each question position exactly once.')
    questions = {q['position']: q for q in candidate['quiz']['questions']}
    keys = {k['position']: k['correct_option'] for k in candidate['answer_key']}
    explanation = handoff['payload']['explanation_text']
    judgments, issues, uncertainty = [], [], []
    for q in report['questions']:
        pos = q['position']
        if q['concept_id'] != questions[pos]['concept_id']:
            raise ReviewRejected('Reviewer changed the question concept.')
        if any(not quote.strip() or quote not in explanation for quote in q['support_quotes']):
            raise ReviewRejected('Support quotes must occur in the supplied explanation.')
        if q['grounding'] == 'pass' and not q['support_quotes']:
            raise ReviewRejected('A grounded pass requires a supporting quote.')
        if q['exactly_one_answer'] == 'pass' and q['defensible_options'] != [keys[pos]]:
            raise ReviewRejected('A single-answer pass must identify exactly the saved keyed option.')
        if any(q[c] == 'fail' for c in CRITERIA) and not q['issues']:
            raise ReviewRejected('Failed criteria require actionable issues.')
        if any(q[c] == 'uncertain' for c in CRITERIA) and not q['uncertainty_notes']:
            raise ReviewRejected('Uncertain criteria require an uncertainty note.')
        judgments.extend(q[c] for c in CRITERIA)
        issues.extend(q['issues'])
        uncertainty.extend(q['uncertainty_notes'])
    overall = report['quiz_review']
    if 'fail' in (overall['concept_coverage'], overall['target_diversity']) and not overall['issues']:
        raise ReviewRejected('Failed quiz-level criteria require actionable issues.')
    if 'uncertain' in (overall['concept_coverage'], overall['target_diversity']) and not overall['uncertainty_notes']:
        raise ReviewRejected('Uncertain quiz-level criteria require an uncertainty note.')
    # A failed target_diversity is soft: it is explained by repeated_target (or a hard issue code,
    # which still blocks through reasons). An uncertain one still blocks.
    judgments.extend([overall['concept_coverage'], 'uncertain' if overall['target_diversity'] == 'uncertain' else 'pass'])
    issues.extend(overall['issues'])
    uncertainty.extend(overall['uncertainty_notes'])
    understanding = sum(q['primary_demand'] in UNDERSTANDING for q in report['questions'])
    codes = {i['code'] for i in issues}
    question_failed = any(q[c] == 'fail' for q in report['questions'] for c in CRITERIA)
    soft = SOFT_CODES | (set() if question_failed else CONDITIONAL_SOFT_CODES)
    reasons = sorted(codes - soft)
    warnings = sorted((codes & soft) | ({'insufficient_understanding'} if understanding < minimum_understanding else set()))
    if 'uncertain' in judgments or uncertainty:
        decision = 'blocked'
        reasons.append('review_uncertainty')
    elif final_revision and not (set(reasons) - FINAL_REVIEW_SOFT_CODES) and not any(
            q[c] == 'fail' for q in report['questions'] for c in CRITERIA if c not in FINAL_REVIEW_SOFT_CRITERIA):
        warnings = sorted(set(warnings) | set(reasons) | {'revision_published_with_warnings'})
        reasons, decision = [], 'pass'
    elif 'fail' in judgments or reasons:
        decision = 'needs_revision'
    else:
        decision = 'pass'
    return dict(decision=decision, candidate_hash=expected['candidate_hash'], understanding_count=understanding,
                reasons=sorted(set(reasons)), warnings=warnings)
