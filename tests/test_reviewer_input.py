"""Saved private candidate -> Reviewer request; no model calls."""
import copy
import json
import os
from pathlib import Path
import subprocess
import time
import unittest

from tests import test_quality_storage as fixtures
from study_buddy.quality_state import QualityStateRejected, reserve_quality_call
from study_buddy.quality_storage import build_private_reviewer_context, store_review_report, store_candidate

ROOT = Path(__file__).resolve().parent.parent
LEARNER = fixtures.LEARNER


class ReviewerInputTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.QualityStorageTests(); self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn = self.helper.conn
        self.call = self.helper.prepare_review()['call_id']

    def context(self):
        return build_private_reviewer_context(self.conn, LEARNER, self.call)

    def test_saved_payload_and_no_new_budget_or_publication(self):
        before = list(self.conn.iterdump())
        context = self.context()
        self.assertEqual(context['candidate'], self.helper.candidate)
        self.assertEqual(json.loads(context['candidate_canonical_json']), context['candidate'])
        self.assertEqual(context['controller_context']['candidate_version'], 0)
        self.assertEqual(context['teaching']['focus_concept_ids'], [])
        self.assertEqual(before, list(self.conn.iterdump()))
        self.assertNotIn('learner_id', json.dumps(context))

    def test_wrong_owner_and_generator_call_are_rejected(self):
        with self.assertRaises(QualityStateRejected):
            build_private_reviewer_context(self.conn, 'learner_other', self.call)
        with self.assertRaises(QualityStateRejected):
            build_private_reviewer_context(self.conn, LEARNER, self.helper.call['call_id'])

    def test_completed_review_cannot_build_another_request(self):
        store_review_report(self.conn, LEARNER, self.call, self.helper.report())
        with self.assertRaises(QualityStateRejected): self.context()

    def test_expired_and_stale_reservations_are_rejected(self):
        self.conn.execute('UPDATE quiz_quality_operations SET started_at = ?, deadline_at = ?', (time.time() - 181, time.time() - 1))
        self.conn.commit()
        with self.assertRaises(QualityStateRejected): self.context()
        self.conn.execute('UPDATE quiz_quality_operations SET deadline_at = ?', (time.time() + 120,))
        self.conn.execute("UPDATE quiz_quality_calls SET candidate_hash = ? WHERE call_id = ?", ('0' * 64, self.call))
        self.conn.commit()
        with self.assertRaises(QualityStateRejected): self.context()

    def test_unapproved_source_and_tampered_teaching_are_rejected(self):
        self.conn.execute('UPDATE lessons SET approved = 0'); self.conn.commit()
        with self.assertRaises(QualityStateRejected): self.context()
        self.conn.execute('UPDATE lessons SET approved = 1')
        self.conn.execute("UPDATE explanations SET text = text || 'changed'"); self.conn.commit()
        with self.assertRaises(QualityStateRejected): self.context()

    def test_revision_uses_new_saved_candidate_without_previous_report(self):
        report = self.helper.report()
        report['questions'][0]['plausible_distractors'] = 'fail'
        report['questions'][0]['issues'] = [dict(code='weak_distractor', detail='Too easy.', related_positions=[])]
        store_review_report(self.conn, LEARNER, self.call, report)
        revision = reserve_quality_call(self.conn, LEARNER, self.helper.op, 'revision')
        candidate = copy.deepcopy(self.helper.candidate)
        candidate['quiz']['questions'][0]['prompt'] += ' Revised.'
        store_candidate(self.conn, LEARNER, revision['call_id'], candidate)
        self.call = reserve_quality_call(self.conn, LEARNER, self.helper.op, 'review_revision')['call_id']
        context = self.context()
        self.assertEqual(context['controller_context']['candidate_version'], 1)
        self.assertEqual(context['candidate'], candidate)
        self.assertNotIn('Too easy.', json.dumps(context))

    def test_real_python_serialization_arrives_intact_at_javascript(self):
        node = os.environ.get('STUDY_BUDDY_NODE', 'node')
        program = """import {buildReviewerInput} from './backend/reviewer-input.mjs';
let data = ''; for await (const chunk of process.stdin) data += chunk;
process.stdout.write(JSON.stringify(buildReviewerInput(JSON.parse(data))));"""
        result = subprocess.run([node, '--input-type=module', '-e', program], cwd=ROOT,
                                input=json.dumps(self.context()), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        request = json.loads(result.stdout)
        payload = json.loads(request['user'])
        self.assertEqual(payload['candidate'], self.helper.candidate)
        self.assertEqual(payload['controller_context'], self.context()['controller_context'])
        self.assertNotIn('candidate_canonical_json', payload)
        self.assertIn('untrusted data', request['system'])

    def test_retry_context_keeps_exact_saved_focus(self):
        from tests import test_retry_quiz as retry_fixtures
        from study_buddy.quality_state import start_quality_operation
        helper = retry_fixtures.RetryQuizTests(); helper.setUp()
        self.addCleanup(helper.doCleanups)
        conn = helper.conn
        sid, data = conn.execute("SELECT session_id, context FROM session_routes WHERE route = 'reteach'").fetchone()
        handoff = json.loads(data)['tutor_handoff']
        op = start_quality_operation(conn, LEARNER, sid, handoff['handoff_id'], handoff['payload']['explanation_id'], 'retry')['operation']['operation_id']
        generation = reserve_quality_call(conn, LEARNER, op, 'generation')
        store_candidate(conn, LEARNER, generation['call_id'], helper.assessment(handoff))
        call = reserve_quality_call(conn, LEARNER, op, 'review')
        context = build_private_reviewer_context(conn, LEARNER, call['call_id'])
        self.assertEqual(context['controller_context']['round'], 'retry')
        self.assertEqual(set(context['teaching']['focus_concept_ids']), set(handoff['payload']['focus_concept_ids']))
        self.assertEqual(context['teaching']['focus_concept_ids'], context['teaching']['concept_ids'])
