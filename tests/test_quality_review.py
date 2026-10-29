"""Offline reviewer-contract tests; no semantic quality claim or provider call."""
import copy
import json
from pathlib import Path
import unittest

from study_buddy.quality_review import VALIDATOR, ReviewRejected, candidate_hash, validate_review

FIXTURES = Path(__file__).resolve().parent / 'fixtures/quiz_quality'


class QualityReviewTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((FIXTURES / 'context.json').read_text())
        self.report = json.loads((FIXTURES / 'pass.json').read_text())['report']

    def test_all_authored_fixture_shapes_and_expected_decisions(self):
        for name in ('pass', 'ambiguous_answers', 'inaccurate_rationale', 'uncertain', 'stale_hash'):
            with self.subTest(case=name):
                case = json.loads((FIXTURES / f'{name}.json').read_text())
                self.assertEqual(list(VALIDATOR.iter_errors(case['report'])), [])
                context = copy.deepcopy(self.context)
                context['candidate'] = case.get('candidate_override', context['candidate'])
                if case['expected_decision'] == 'rejected':
                    with self.assertRaises(ReviewRejected): validate_review(case['report'], context)
                else:
                    self.assertEqual(validate_review(case['report'], context)['decision'], case['expected_decision'])

    def test_confirmed_ambiguity_and_bad_justification_reach_revision(self):
        q = self.report['questions'][0]
        q['exactly_one_answer'] = 'fail'
        q['defensible_options'] = ['a', 'b']
        q['issues'] = [dict(code='ambiguous_answer', detail='Two choices are supported; replace the competing true answer.', related_positions=[1])]
        result = validate_review(self.report, self.context)
        self.assertEqual(result['decision'], 'needs_revision')
        other = self.report['questions'][1]
        other['rationale_quality'] = 'fail'
        other['issues'] = [dict(code='inaccurate_rationale', detail='A correct label is paired with a reason contradicted by the teaching.', related_positions=[2])]
        self.assertEqual(validate_review(self.report, self.context)['decision'], 'needs_revision')

    def test_a_known_question_defect_never_overrides_genuine_uncertainty(self):
        q = self.report['questions'][0]
        q['exactly_one_answer'] = 'fail'
        q['defensible_options'] = ['a', 'b']
        q['issues'] = [dict(code='ambiguous_answer', detail='Two answers are supported.', related_positions=[1])]
        other = self.report['questions'][1]
        other['grounding'] = 'uncertain'
        other['uncertainty_notes'] = ['The explanation does not resolve whether the proposed justification is valid.']
        result = validate_review(self.report, self.context)
        self.assertEqual(result['decision'], 'blocked')
        self.assertIn('review_uncertainty', result['reasons'])

    def test_duplicate_or_missing_question_position_is_rejected(self):
        self.report['questions'][1]['position'] = 1
        with self.assertRaises(ReviewRejected): validate_review(self.report, self.context)
        self.report['questions'].pop()
        with self.assertRaises(ReviewRejected): validate_review(self.report, self.context)

    def test_all_echoed_binding_fields_are_checked(self):
        for field, value in dict(operation_id='quality_z1b2c3d4e5f6', session_id='session_stale', lesson_id='lesson_other',
                                 lesson_version=99, handoff_id='handoff_stale', explanation_id='expl_stale',
                                 round='retry', candidate_version=1).items():
            r = copy.deepcopy(self.report); r[field] = value
            with self.subTest(field=field), self.assertRaises(ReviewRejected): validate_review(r, self.context)

    def test_candidate_options_keys_and_rationales_invalidate_approval(self):
        for change in ('option', 'key', 'rationale'):
            context = copy.deepcopy(self.context)
            if change == 'option': context['candidate']['quiz']['questions'][0]['options']['a'] += ' Changed.'
            if change == 'key': context['candidate']['answer_key'][0]['correct_option'] = 'b'
            if change == 'rationale': context['candidate']['answer_key'][0]['rationale'] += ' Changed.'
            with self.subTest(change=change), self.assertRaises(ReviewRejected): validate_review(self.report, context)

    def test_quote_checks_reject_blank_absent_and_missing_support(self):
        for quotes in (['   '], ['A claim not present in the source.'], []):
            r = copy.deepcopy(self.report); r['questions'][0]['support_quotes'] = quotes
            with self.subTest(quotes=quotes), self.assertRaises(ReviewRejected): validate_review(r, self.context)

    def test_pass_requires_the_saved_key_and_same_concept(self):
        for options in ([], ['a', 'b'], ['b']):
            r = copy.deepcopy(self.report); r['questions'][0]['defensible_options'] = options
            with self.subTest(options=options), self.assertRaises(ReviewRejected): validate_review(r, self.context)
        self.report['questions'][0]['concept_id'] = 'c_other'
        with self.assertRaises(ReviewRejected): validate_review(self.report, self.context)

    def test_unknown_fields_overall_approval_and_invalid_option_are_rejected(self):
        for mutate in (lambda r: r.update(overall_pass=True),
                       lambda r: r['questions'][0].update(score=10),
                       lambda r: r['questions'][0].update(defensible_options=['e'])):
            r = copy.deepcopy(self.report); mutate(r)
            with self.assertRaises(ReviewRejected): validate_review(r, self.context)

    def test_failed_or_uncertain_judgments_require_evidence_notes(self):
        self.report['questions'][0]['grounding'] = 'fail'
        with self.assertRaises(ReviewRejected): validate_review(self.report, self.context)
        self.report['questions'][0]['grounding'] = 'uncertain'
        with self.assertRaises(ReviewRejected): validate_review(self.report, self.context)

    def test_hard_quiz_issue_blocks_and_repeated_target_only_warns(self):
        self.report['quiz_review']['concept_coverage'] = 'fail'
        self.report['quiz_review']['issues'] = [dict(code='missing_coverage', detail='A taught concept is untested.', related_positions=[1, 2])]
        self.assertEqual(validate_review(self.report, self.context)['decision'], 'needs_revision')
        self.report['quiz_review']['issues'][0]['related_positions'] = [11]
        with self.assertRaises(ReviewRejected): validate_review(self.report, self.context)
        soft = copy.deepcopy(json.loads((FIXTURES / 'pass.json').read_text())['report'])
        soft['quiz_review']['target_diversity'] = 'fail'
        soft['quiz_review']['issues'] = [dict(code='repeated_target', detail='Items repeat a target.', related_positions=[1, 2])]
        result = validate_review(soft, self.context)
        self.assertEqual((result['decision'], result['reasons'], result['warnings']), ('pass', [], ['repeated_target']))

    def test_controller_counts_understanding_and_only_warns_when_below_the_goal(self):
        self.assertEqual(validate_review(self.report, self.context)['understanding_count'], 6)
        self.assertEqual(validate_review(self.report, self.context)['warnings'], [])
        for q in self.report['questions']: q['primary_demand'] = 'evidence_boundary'
        result = validate_review(self.report, self.context)
        self.assertEqual(result['understanding_count'], 0)
        self.assertEqual((result['decision'], result['reasons']), ('pass', []))
        self.assertEqual(result['warnings'], ['insufficient_understanding'])

    def test_answer_cue_only_warns_when_no_question_criterion_failed(self):
        cue = dict(code='answer_cue', detail='The keyed option is the longest.', related_positions=[3])
        self.report['quiz_review']['issues'] = [cue]
        result = validate_review(self.report, self.context)
        self.assertEqual((result['decision'], result['reasons'], result['warnings']), ('pass', [], ['answer_cue']))
        q = self.report['questions'][2]
        q.update(fair_wording='fail', issues=[cue])
        result = validate_review(self.report, self.context)
        self.assertEqual((result['decision'], result['reasons']), ('needs_revision', ['answer_cue']))

    def test_final_revision_publishes_distractor_and_wording_failures_with_warnings(self):
        cue = dict(code='answer_cue', detail='The keyed option is the longest.', related_positions=[3])
        weak = dict(code='weak_distractor', detail='Wrong choices are easy to eliminate.', related_positions=[4])
        self.report['questions'][2].update(fair_wording='fail', issues=[cue])
        self.report['questions'][3].update(plausible_distractors='fail', issues=[weak])
        self.assertEqual(validate_review(self.report, self.context)['decision'], 'needs_revision')
        result = validate_review(self.report, self.context, final_revision=True)
        self.assertEqual((result['decision'], result['reasons']), ('pass', []))
        self.assertIn('revision_published_with_warnings', result['warnings'])
        self.assertIn('weak_distractor', result['warnings'])

    def test_final_revision_still_blocks_content_defects_and_uncertainty(self):
        q = self.report['questions'][0]
        q.update(exactly_one_answer='fail', defensible_options=['a', 'b'],
                 issues=[dict(code='ambiguous_answer', detail='Two choices are supported.', related_positions=[1])])
        self.assertEqual(validate_review(self.report, self.context, final_revision=True)['decision'], 'needs_revision')
        report = json.loads((FIXTURES / 'pass.json').read_text())['report']
        report['questions'][1].update(plausible_distractors='fail',
                                      issues=[dict(code='inaccurate_rationale', detail='Reason is contradicted.', related_positions=[2])])
        report['questions'][1]['rationale_quality'] = 'fail'
        self.assertEqual(validate_review(report, self.context, final_revision=True)['decision'], 'needs_revision')

    def test_repetition_warning_does_not_hide_genuine_content_uncertainty(self):
        self.report['quiz_review'].update(target_diversity='fail', issues=[dict(
            code='repeated_target', detail='Items repeat a target.', related_positions=[1, 2])])
        self.assertEqual(validate_review(self.report, self.context)['decision'], 'pass')
        self.report['questions'][0].update(grounding='uncertain',
            uncertainty_notes=['Cannot determine whether the explanation supports this claim.'])
        result = validate_review(self.report, self.context)
        self.assertEqual(result['decision'], 'blocked')
        self.assertIn('review_uncertainty', result['reasons'])
        self.assertIn('repeated_target', result['warnings'])

    def test_uncertain_target_diversity_still_blocks(self):
        self.report['quiz_review'].update(target_diversity='uncertain', uncertainty_notes=['Cannot tell.'])
        self.assertEqual(validate_review(self.report, self.context)['decision'], 'blocked')

    def test_unsupported_rubric_and_hash_versions_are_rejected(self):
        for field in ('rubric_version', 'hash_version', 'schema_version'):
            r = copy.deepcopy(self.report); r[field] = 'unknown'
            with self.subTest(field=field), self.assertRaises(ReviewRejected): validate_review(r, self.context)

    def test_hash_is_stable_for_object_order_and_sensitive_to_content(self):
        candidate = self.context['candidate']
        reordered = dict(reversed(list(candidate.items())))
        self.assertEqual(candidate_hash(candidate), candidate_hash(reordered))
        reordered['round'] = 'retry'
        self.assertNotEqual(candidate_hash(candidate), candidate_hash(reordered))

    def test_structurally_invalid_candidate_is_not_reviewable(self):
        self.context['candidate']['quiz']['questions'].pop()
        with self.assertRaises(ReviewRejected): validate_review(self.report, self.context)
