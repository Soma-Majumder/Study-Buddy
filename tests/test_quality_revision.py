"""One content revision, bounded structural correction, full second review."""
import copy
import json
import unittest

from tests import test_quality_storage as fixtures
from study_buddy.controller import dispatch_quality_review, dispatch_quality_revision
from study_buddy.quality_state import reserve_quality_call, QualityStateRejected
from study_buddy.reviewer import ReviewerFailure
from study_buddy.assessment import AssessmentFailure

LEARNER = fixtures.LEARNER


class QualityRevisionTests(unittest.TestCase):
    def setUp(self):
        self.helper = fixtures.QualityStorageTests(); self.helper.setUp()
        self.addCleanup(self.helper.doCleanups)
        self.conn, self.op = self.helper.conn, self.helper.op
        self.helper.stage()
        def initial(context, **kwargs):
            r = self.helper.report()
            r['quiz_review']['concept_coverage'] = 'fail'
            r['quiz_review']['issues'] = [dict(code='missing_coverage', detail='Repeated.', related_positions=[1, 2])]
            return r
        dispatch_quality_review(self.conn, LEARNER, self.op, reviewer=initial)
        self.calls = []
        self.review_calls = []
        self.revised = copy.deepcopy(self.helper.candidate)
        self.revised['quiz']['questions'][0]['prompt'] += ' Revised.'

    def assessment(self, context, **kwargs):
        self.assertFalse(self.conn.in_transaction)
        self.calls.append((copy.deepcopy(context), kwargs))
        self.assertEqual(context['original']['candidate'], self.helper.candidate)
        self.assertEqual(context['review_report']['quiz_review']['concept_coverage'], 'fail')
        return copy.deepcopy(self.revised)

    def reviewer(self, context, **kwargs):
        self.review_calls.append(context)
        self.assertEqual(context['candidate'], self.revised)
        self.assertEqual(context['controller_context']['candidate_version'], 1)
        return self.helper.report(1, self.revised)

    def dispatch(self, assessment=None, reviewer=None):
        return dispatch_quality_revision(self.conn, LEARNER, self.op, assessment=assessment or self.assessment,
                                         reviewer=reviewer or self.reviewer)

    def test_one_revision_then_full_second_review_no_publication(self):
        result = self.dispatch()
        self.assertEqual(result['stage'], 'approved')
        self.assertEqual(len(self.calls), 1); self.assertEqual(len(self.review_calls), 1)
        self.assertFalse(self.calls[0][1]['technical_repair'])
        self.assertEqual(self.conn.execute('SELECT revision_count, review_count FROM quiz_quality_operations').fetchone(), (1, 2))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_candidates').fetchone()[0], 2)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0], 0)
        self.assertFalse(self.dispatch()['dispatched'])
        self.assertEqual(len(self.calls), 1)

    def test_malformed_revision_gets_only_remaining_shared_technical_repair(self):
        def assessment(context, **kwargs):
            output = self.assessment(context, **kwargs)
            return output if kwargs['technical_repair'] else None
        self.assertEqual(self.dispatch(assessment=assessment)['stage'], 'approved')
        self.assertEqual([c[1]['technical_repair'] for c in self.calls], [False, True])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_calls').fetchone()[0], 5)
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0], 1)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0], 0)

    def test_already_used_technical_repair_is_not_reset(self):
        self.conn.execute('UPDATE handoff_repairs SET repair_count=1'); self.conn.commit()
        def assessment(context, **kwargs): self.assessment(context, **kwargs); return None
        result = self.dispatch(assessment=assessment)
        self.assertEqual(result['error'], 'technical_repair_exhausted')
        self.assertEqual(len(self.calls), 1); self.assertEqual(self.review_calls, [])

    def test_two_invalid_outputs_stop_without_review_or_third_candidate(self):
        def assessment(context, **kwargs): self.assessment(context, **kwargs); return {}
        result = self.dispatch(assessment=assessment)
        self.assertEqual(result['error'], 'technical_repair_exhausted')
        self.assertEqual(len(self.calls), 2); self.assertEqual(self.review_calls, [])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_candidates').fetchone()[0], 1)

    def test_second_content_failure_stops_without_another_revision(self):
        def reviewer(context, **kwargs):
            r = self.reviewer(context, **kwargs)
            r['quiz_review']['concept_coverage'] = 'fail'
            r['quiz_review']['issues'] = [dict(code='missing_coverage', detail='Still repeated.', related_positions=[1, 2])]
            return r
        result = self.dispatch(reviewer=reviewer)
        self.assertEqual(result['error'], 'content_revision_exhausted')
        self.assertEqual(self.conn.execute('SELECT status FROM study_sessions').fetchone()[0], 'failed')
        self.dispatch(); self.assertEqual(len(self.calls), 1)

    def test_provider_failure_is_terminal_without_structural_repair(self):
        def assessment(context, **kwargs): raise AssessmentFailure(code='request_timeout')
        result = self.dispatch(assessment=assessment)
        self.assertEqual(result['error'], 'revision_request_timeout')
        self.assertEqual(self.conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0], 0)
        self.assertEqual(self.review_calls, [])

    def test_crashed_revision_reservation_cannot_be_resent(self):
        reserve_quality_call(self.conn, LEARNER, self.op, 'revision')
        result = self.dispatch()
        self.assertFalse(result['dispatched']); self.assertEqual(result['stage'], 'revising')
        self.assertEqual(self.calls, [])

    def test_wrong_owner_and_tampered_saved_report_stop_before_call(self):
        with self.assertRaises(QualityStateRejected):
            dispatch_quality_revision(self.conn, 'learner_other', self.op, assessment=self.assessment)
        self.conn.execute("UPDATE quiz_quality_reports SET report_data='{}'"); self.conn.commit()
        result = self.dispatch()
        self.assertEqual(result['error'], 'revision_context_invalid'); self.assertEqual(self.calls, [])

    def test_candidate_save_failure_records_failure_and_no_replacement(self):
        self.conn.execute("CREATE TRIGGER break_revision BEFORE INSERT ON quiz_quality_candidates WHEN NEW.candidate_version=1 BEGIN SELECT RAISE(ABORT, 'save error'); END")
        self.conn.commit()
        result = self.dispatch()
        self.assertEqual(result['error'], 'revision_save_failed')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_candidates').fetchone()[0], 1)
        self.assertEqual(self.review_calls, [])

    def test_default_revision_and_reviewer_bridges_complete_offline(self):
        import os
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            loader = Path(directory) / 'fake-fetch.mjs'
            loader.write_text(r'''import {readFile, appendFile} from 'node:fs/promises';
globalThis.fetch = async (_url, request) => {
  const body = JSON.parse(request.body); const data = JSON.parse(body.messages[0].content);
  let output;
  const revision = !!data.review_report;
  await appendFile(process.env.REVISION_TEST_LOG, revision ? 'revision\n' : 'review\n');
  if (revision) {
    const quiz = structuredClone(data.candidate);
    quiz.quiz.questions[0].prompt += ' Revised.';
    output = {result:quiz};
  } else {
    output = JSON.parse(await readFile('tests/fixtures/quiz_quality/pass.json','utf8')).report;
    Object.assign(output,data.controller_context);
    for (const q of output.questions) {
      q.concept_id = data.candidate.quiz.questions.find(c=>c.position===q.position).concept_id;
      q.defensible_options=[data.candidate.answer_key.find(k=>k.position===q.position).correct_option];
    }
  }
  return {ok:true,json:async()=>({stop_reason:'end_turn',content:[{type:'text',text:JSON.stringify(output)}],
    model:'offline',usage:{input_tokens:1,output_tokens:1}})};
};
''')
            log = Path(directory) / 'calls.txt'
            with patch.dict(os.environ, {'STUDY_BUDDY_ALLOW_PAID_CLAUDE': 'true', 'NODE_OPTIONS':f'--import={loader}', 'ANTHROPIC_API_KEY':'fake-key',
                                         'REVISION_TEST_LOG':str(log)}):
                result = dispatch_quality_revision(self.conn, LEARNER, self.op)
                self.assertEqual(result['stage'], 'approved')
                self.assertFalse(dispatch_quality_revision(self.conn, LEARNER, self.op)['dispatched'])
            self.assertEqual(log.read_text().splitlines(), ['revision', 'review'])
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0], 2)

    def test_expired_revision_does_not_dispatch(self):
        import time
        self.conn.execute('UPDATE quiz_quality_operations SET started_at=?, deadline_at=?', (time.time()-181,time.time()-1))
        self.conn.commit()
        result = self.dispatch()
        self.assertEqual(result['error'], 'quality_deadline'); self.assertEqual(self.calls, [])

    def test_saved_replacement_can_resume_second_review_without_resending_revision(self):
        from study_buddy.quality_storage import store_candidate
        grant = reserve_quality_call(self.conn, LEARNER, self.op, 'revision')
        store_candidate(self.conn, LEARNER, grant['call_id'], self.revised)
        result = self.dispatch()
        self.assertEqual(result['stage'], 'approved')
        self.assertEqual(self.calls, []); self.assertEqual(len(self.review_calls), 1)

    def test_concurrent_revision_requests_send_only_one_content_revision(self):
        from concurrent.futures import ThreadPoolExecutor
        from contextlib import closing
        from pathlib import Path
        import sqlite3
        import tempfile
        import threading
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'quality.db'
            with closing(sqlite3.connect(path)) as target: self.conn.backup(target)
            entered, release = threading.Event(), threading.Event()
            calls = []
            def assessment(context, **kwargs):
                calls.append(1); entered.set()
                if not release.wait(5): raise RuntimeError('Test synchronization failed')
                return copy.deepcopy(self.revised)
            def reviewer(context, **kwargs): return self.helper.report(1,self.revised)
            def dispatch():
                with closing(sqlite3.connect(path, timeout=5)) as conn:
                    conn.execute('PRAGMA foreign_keys=ON')
                    return dispatch_quality_revision(conn, LEARNER, self.op, assessment=assessment, reviewer=reviewer)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(dispatch)
                try:
                    self.assertTrue(entered.wait(5))
                    self.assertFalse(pool.submit(dispatch).result(timeout=5)['dispatched'])
                finally: release.set()
                self.assertEqual(first.result(timeout=5)['stage'], 'approved')
            self.assertEqual(calls,[1])
