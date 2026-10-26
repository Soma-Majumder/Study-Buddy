"""Offline checks for labelled benchmark fixtures; no semantic quality claim or provider call.

Each benchmark_*.json is a realistic defective or soft-warning candidate with an authored report.
These tests prove the controller derives the expected decision, blocking reasons and warnings from
that report. They do NOT prove a live Reviewer finds the defect: that needs a live evaluation that
feeds each candidate to the Reviewer and compares its verdict with these labels.
"""
import json
from pathlib import Path
import unittest

from study_buddy.quality_review import SOFT_CODES, UNDERSTANDING_GOAL, VALIDATOR, validate_review

FIXTURES = Path(__file__).resolve().parent / 'fixtures/quiz_quality'
CASES = sorted(FIXTURES.glob('benchmark_*.json'))
SOFT_ONLY = {'repeated_target_bilayer', 'recall_heavy_all_items_pass', 'understanding_boundary_five',
             'strict_labels_reference', 'understanding_three'}


class QualityBenchmarkFixtureTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((FIXTURES / 'context.json').read_text())

    def case_result(self, case):
        context = dict(self.context, candidate=case.get('candidate_override', self.context['candidate']))
        return validate_review(case['report'], context)

    def test_each_case_has_expected_decision_reasons_and_warnings(self):
        self.assertGreaterEqual(len(CASES), 8)
        for path in CASES:
            case = json.loads(path.read_text())
            with self.subTest(case=case['name']):
                self.assertEqual(path.stem, 'benchmark_' + case['name'])
                self.assertTrue(case['known_defect'] and case['source'])
                self.assertEqual(list(VALIDATOR.iter_errors(case['report'])), [])
                result = self.case_result(case)
                self.assertEqual(result['decision'], case['expected_decision'])
                self.assertEqual(result['reasons'], case['expected_reasons'])
                self.assertEqual(result['warnings'], case['expected_warnings'])

    def test_hard_defects_block_and_soft_cases_only_warn(self):
        for path in CASES:
            case = json.loads(path.read_text())
            with self.subTest(case=case['name']):
                if case['name'] in SOFT_ONLY:
                    self.assertEqual((case['expected_decision'], case['expected_reasons']), ('pass', []))
                    self.assertTrue(set(case['expected_warnings']) <= SOFT_CODES)
                else:
                    self.assertEqual(case['expected_decision'], 'needs_revision')
                    self.assertTrue(case['expected_reasons'])

    def test_understanding_counts_match_the_labelled_cases(self):
        counts = {'understanding_boundary_five': 5, 'strict_labels_reference': 4, 'understanding_three': 3,
                  'recall_heavy_all_items_pass': 2}
        for name, expected in counts.items():
            case = json.loads((FIXTURES / f'benchmark_{name}.json').read_text())
            with self.subTest(case=name):
                self.assertEqual(self.case_result(case)['understanding_count'], expected)
                self.assertLess(expected, UNDERSTANDING_GOAL)
        pass_report = json.loads((FIXTURES / 'pass.json').read_text())['report']
        result = validate_review(pass_report, self.context)
        self.assertEqual((result['understanding_count'], result['warnings']), (UNDERSTANDING_GOAL, []))
