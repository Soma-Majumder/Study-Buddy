"""Offline checks for labelled benchmark fixtures; no semantic quality claim or provider call.

Each benchmark_*.json is a realistic defective (or policy-edge) candidate with an authored
report. These tests prove the controller derives the expected decision and reasons from that
report. They do NOT prove a live Reviewer finds the defect: that needs a live evaluation that
feeds candidate (or the context candidate) to the Reviewer and compares its verdict.
"""
import json
from pathlib import Path
import unittest

from study_buddy.quality_review import VALIDATOR, validate_review

FIXTURES = Path(__file__).resolve().parent / 'fixtures/quiz_quality'
CASES = sorted(FIXTURES.glob('benchmark_*.json'))


class QualityBenchmarkFixtureTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((FIXTURES / 'context.json').read_text())

    def test_benchmark_cases_exist(self):
        self.assertGreaterEqual(len(CASES), 7)

    def test_each_case_has_expected_decision_and_reasons(self):
        for path in CASES:
            case = json.loads(path.read_text())
            with self.subTest(case=case['name']):
                self.assertEqual(path.stem, 'benchmark_' + case['name'])
                self.assertTrue(case['known_defect'] and case['source'])
                self.assertEqual(list(VALIDATOR.iter_errors(case['report'])), [])
                context = dict(self.context, candidate=case.get('candidate_override', self.context['candidate']))
                result = validate_review(case['report'], context)
                self.assertEqual(result['decision'], case['expected_decision'])
                self.assertEqual(result['reasons'], case['expected_reasons'])

    def test_no_benchmark_defect_is_approved(self):
        for path in CASES:
            case = json.loads(path.read_text())
            with self.subTest(case=case['name']):
                self.assertNotEqual(case['expected_decision'], 'pass')

    def test_strict_labels_show_reference_quiz_falls_below_six(self):
        case = json.loads((FIXTURES / 'benchmark_strict_labels_reference.json').read_text())
        result = validate_review(case['report'], self.context)
        self.assertEqual(result['understanding_count'], 4)
        pass_report = json.loads((FIXTURES / 'pass.json').read_text())['report']
        self.assertEqual(validate_review(pass_report, self.context)['understanding_count'], 6)

    def test_understanding_boundary_is_exactly_six(self):
        five = json.loads((FIXTURES / 'benchmark_understanding_boundary_five.json').read_text())
        self.assertEqual(validate_review(five['report'], self.context)['understanding_count'], 5)
        pass_report = json.loads((FIXTURES / 'pass.json').read_text())['report']
        self.assertEqual(validate_review(pass_report, self.context)['decision'], 'pass')
        self.assertEqual(validate_review(five['report'], self.context, minimum_understanding=5)['decision'], 'pass')

    def test_proposed_threshold_of_four_blocks_only_clearly_recall_heavy_quizzes(self):
        outcomes = {}
        for name in ('understanding_boundary_five', 'strict_labels_reference', 'recall_heavy_all_items_pass'):
            case = json.loads((FIXTURES / f'benchmark_{name}.json').read_text())
            outcomes[name] = validate_review(case['report'], self.context, minimum_understanding=4)['decision']
        self.assertEqual(outcomes, dict(understanding_boundary_five='pass', strict_labels_reference='pass',
                                        recall_heavy_all_items_pass='needs_revision'))
