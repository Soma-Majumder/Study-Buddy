"""Private, offline option-length diagnostic. Flags are not quality verdicts."""
import math


def option_balance_issues(candidate, max_ratio=1.3):
    """Compare keyed option character length with the mean of the other three.

    Uses the canonical private quiz/key shape. Never returns answer letters or
    option text. Do not use this heuristic as permission to publish or as an
    extra revision budget; this module makes no calls and changes no state.
    """
    if type(max_ratio) not in (int, float) or not math.isfinite(max_ratio) or max_ratio <= 1:
        raise ValueError('Balance threshold must be finite and greater than one.')
    questions = candidate['quiz']['questions']
    key_list = candidate['answer_key']
    keys = {k['position']: k['correct_option'] for k in key_list}
    positions = [q['position'] for q in questions]
    if len(keys) != len(key_list) or len(set(positions)) != len(positions) or set(keys) != set(positions):
        raise ValueError('Questions and keys must have unique matching positions.')
    issues = []
    for q in questions:
        options = q['options']
        correct = keys[q['position']]
        if (set(options) != set('abcd') or correct not in options
                or any(not isinstance(v, str) or not v.strip() for v in options.values())):
            raise ValueError('Each question needs four nonblank options and a valid key.')
        # Trim incidental edge whitespace; count characters, not tokens or words.
        lengths = {letter: len(text.strip()) for letter, text in options.items()}
        other_average = sum(n for letter, n in lengths.items() if letter != correct) / 3
        ratio = lengths[correct] / other_average
        if ratio > max_ratio:
            issues.append(dict(position=q['position'], code='option_length_flag', ratio=round(ratio, 3)))
    return issues
