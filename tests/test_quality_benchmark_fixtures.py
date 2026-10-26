"""Offline checks for labelled benchmark fixtures; no semantic quality claim or provider call.

Each benchmark_*.json is a realistic defective (or policy-edge) candidate with an authored
report. These tests prove the controller derives the expected decision and reasons from that
report. They do NOT prove a live Reviewer finds the defect: that needs a live evaluation that
feeds candidate (or the context candidate) to the Reviewer and compares its verdict.
"""
import json
from pathlib import Path
import unittest

from study_buddy.quality_review import MINIMUM_UNDERSTANDING, UNDERSTANDING_GOAL, VALIDATOR, validate_review

FIXTURES = Path(__file__).resolve().parent / 'fixtures/quiz_quality'
CASES = sorted(FIXTURES.glob('benchmark_*.json'))


class QualityBenchmarkFixtureTests(unittest.TestCase):
    def setUp(self):
        self.context = json.loads((FIXTURES / 'context.json').read_text())

    def test_benchmark_cases_exist(self):
        self.assertGreaterEqual(len(CASES), 8)

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

    def test_defects_never_pass_and_threshold_edges_follow_policy(self):
        edges = {'understanding_boundary_five': 5, 'strict_labels_reference': 4, 'understanding_below_minimum_three': 3}
        for path in CASES:
            case = json.loads(path.read_text())
            with self.subTest(case=case['name']):
                if case['name'] in edges:
                    result = validate_review(case['report'], self.context)
                    self.assertEqual(result['understanding_count'], edges[case['name']])
                    self.assertEqual(case['expected_decision'], 'pass' if edges[case['name']] >= MINIMUM_UNDERSTANDING else 'needs_revision')
                else:
                    self.assertNotEqual(case['expected_decision'], 'pass')

    def test_goal_is_six_and_gate_blocks_only_below_four(self):
        self.assertEqual((UNDERSTANDING_GOAL, MINIMUM_UNDERSTANDING), (6, 4))
        pass_report = json.loads((FIXTURES / 'pass.json').read_text())['report']
        self.assertEqual(validate_review(pass_report, self.context)['understanding_count'], UNDERSTANDING_GOAL)
        recall_heavy = json.loads((FIXTURES / 'benchmark_recall_heavy_all_items_pass.json').read_text())
        self.assertEqual(validate_review(recall_heavy['report'], self.context)['decision'], 'needs_revision')
        # The older strict goal-as-gate behaviour remains available explicitly.
        five = json.loads((FIXTURES / 'benchmark_understanding_boundary_five.json').read_text())
        self.assertEqual(validate_review(five['report'], self.context, minimum_understanding=UNDERSTANDING_GOAL)['decision'], 'needs_revision')
