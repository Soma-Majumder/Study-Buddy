"""Learner routes cannot return a quiz until reviewed and published."""
import copy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import test_controller as fixtures
from tests import test_retry_quiz as retry_fixtures
from tests.quality_fixtures import passing_review
from study_buddy.controller import start_quiz_session, continue_quality_quiz, dispatch_retry_quiz
from study_buddy.quality_state import QualityStateRejected

LEARNER=fixtures.LEARNER


def uncertain(context,**kwargs):
    r=passing_review(context)
    r['questions'][0]['grounding']='uncertain';r['questions'][0]['uncertainty_notes']=['Cannot verify.']
    return r


def content_failure(context,**kwargs):
    r=passing_review(context)
    r['quiz_review']['target_diversity']='fail'
    r['quiz_review']['issues']=[dict(code='repeated_target',detail='Repeated target.',related_positions=[1,2])]
    return r


class QuizPipelineTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.ControllerTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.conn=self.helper.conn;self.helper.assessment_calls=[]

    def start(self,**kwargs):
        return start_quiz_session(self.conn,LEARNER,'lesson_cell_membranes',tutor=self.helper.tutor,
                                  assessment=kwargs.pop('assessment',self.helper.assessment),
                                  reviewer=kwargs.pop('reviewer',passing_review),**kwargs)

    def test_uncertain_review_returns_no_quiz_and_saves_failure(self):
        result=self.start(reviewer=uncertain)
        self.assertEqual((result['status'],result['error']),('failed','review_uncertainty'))
        self.assertIsNone(result['quiz'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_reports').fetchone()[0],1)

    def test_repair_then_revision_and_second_review_have_at_most_five_calls(self):
        def assessment(h,repair_request=None,**kwargs):
            q=self.helper.assessment(h,repair_request=repair_request)
            return q if repair_request else None
        def revision(context,**kwargs):
            q=copy.deepcopy(context['original']['candidate']);q['quiz']['questions'][0]['prompt']+=' Revised.'
            return q
        def reviewer(context,**kwargs):
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],0)
            return content_failure(context) if context['controller_context']['candidate_version']==0 else passing_review(context)
        result=self.start(assessment=assessment,reviewer=reviewer,revision_assessment=revision)
        self.assertEqual(result['status'],'awaiting_answers')
        self.assertTrue(result['quiz']['questions'][0]['prompt'].endswith(' Revised.'))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_calls').fetchone()[0],5)
        self.assertEqual(self.conn.execute('SELECT candidate_version FROM quiz_quality_publications').fetchone()[0],1)
        self.assertEqual(self.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0],0)

    def test_second_content_failure_never_returns_questions(self):
        result=self.start(reviewer=content_failure,revision_assessment=lambda c,**kw:copy.deepcopy(c['original']['candidate']))
        self.assertEqual(result['error'],'content_revision_exhausted');self.assertIsNone(result['quiz'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],0)

    def test_publication_save_retry_reuses_approval_and_agent_calls(self):
        self.conn.execute("CREATE TRIGGER fail_ready BEFORE UPDATE ON study_sessions WHEN NEW.status='awaiting_answers' BEGIN SELECT RAISE(ABORT,'failure'); END");self.conn.commit()
        result=self.start();self.assertEqual(result['operation_status'],'pending_save')
        self.assertIsNone(result['quiz'])
        self.conn.execute('DROP TRIGGER fail_ready');self.conn.commit()
        def forbidden(*args,**kwargs):self.fail('No additional agent calls')
        resumed=continue_quality_quiz(self.conn,LEARNER,result['operation_id'],assessment=forbidden,reviewer=forbidden)
        self.assertEqual(resumed['status'],'awaiting_answers')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_calls').fetchone()[0],2)
        with self.assertRaises(QualityStateRejected):continue_quality_quiz(self.conn,'learner_other',result['operation_id'])

    def test_reentrant_continuation_never_resends_inflight_generation(self):
        def assessment(h,**kwargs):
            op=self.conn.execute('SELECT operation_id FROM quiz_quality_operations').fetchone()[0]
            pending=continue_quality_quiz(self.conn,LEARNER,op,assessment=lambda *a,**k:self.fail('duplicate'))
            self.assertEqual(pending['operation_status'],'running');self.assertIsNone(pending['quiz'])
            return self.helper.assessment(h)
        self.assertEqual(self.start(assessment=assessment)['status'],'awaiting_answers')
        self.assertEqual(len(self.helper.assessment_calls),1)

    def test_retry_review_failure_preserves_first_quiz_and_consumed_retry(self):
        helper=retry_fixtures.RetryQuizTests();helper.setUp();self.addCleanup(helper.doCleanups)
        result=dispatch_retry_quiz(helper.conn,LEARNER,helper.attempt_id,assessment=helper.assessment,reviewer=uncertain)
        self.assertEqual(result['status'],'failed');self.assertIsNone(result['quiz'])
        self.assertEqual(helper.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],1)
        self.assertEqual(helper.conn.execute('SELECT learning_retry_count FROM study_sessions').fetchone()[0],1)
        self.assertEqual(helper.conn.execute('SELECT status FROM retry_quiz_operations').fetchone()[0],'failed')

    def test_retry_save_resume_does_not_regenerate_and_hides_quiz_until_saved(self):
        helper=retry_fixtures.RetryQuizTests();helper.setUp();self.addCleanup(helper.doCleanups)
        helper.conn.execute("CREATE TRIGGER fail_ready BEFORE UPDATE ON study_sessions WHEN NEW.status='awaiting_answers' BEGIN SELECT RAISE(ABORT,'failure'); END");helper.conn.commit()
        result=helper.dispatch();self.assertEqual(result['operation_status'],'pending_save');self.assertIsNone(result['quiz'])
        helper.conn.execute('DROP TRIGGER fail_ready');helper.conn.commit()
        result=helper.dispatch();self.assertEqual(result['operation_status'],'ready')
        self.assertEqual(len(helper.calls),1)
        self.assertEqual(helper.conn.execute('SELECT status FROM retry_quiz_operations').fetchone()[0],'ready')

    def test_default_generation_and_review_adapters_work_through_learner_route_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            loader=Path(directory)/'fetch.mjs';log=Path(directory)/'calls.txt'
            loader.write_text(r'''import {readFile,appendFile} from 'node:fs/promises';
globalThis.fetch=async(_url,request)=>{
  const body=JSON.parse(request.body);const data=JSON.parse(body.messages[0].content);let output;
  if(data.handoff){
    await appendFile(process.env.PIPELINE_LOG,'generation\n');
    const q=JSON.parse(await readFile('schemas/v1/examples/quiz_output.example.json','utf8'));
    Object.assign(q,{handoff_id:data.handoff.handoff_id,explanation_id:data.handoff.payload.explanation_id,round:data.handoff.context.round});
    output={result:q};
  }else{
    await appendFile(process.env.PIPELINE_LOG,'review\n');
    output=JSON.parse(await readFile('tests/fixtures/quiz_quality/pass.json','utf8')).report;
    Object.assign(output,data.controller_context);
    for(const q of output.questions){q.concept_id=data.candidate.quiz.questions.find(c=>c.position===q.position).concept_id;
      q.defensible_options=[data.candidate.answer_key.find(k=>k.position===q.position).correct_option];q.support_quotes=[data.teaching.explanation_text];}
  }
  return {ok:true,json:async()=>({stop_reason:'end_turn',content:[{type:'text',text:JSON.stringify(output)}],model:'offline',usage:{input_tokens:1,output_tokens:1}})};
};''')
            with patch.dict(os.environ,{'STUDY_BUDDY_ALLOW_PAID_CLAUDE': 'true', 'NODE_OPTIONS':f'--import={loader}','ANTHROPIC_API_KEY':'fake-key','PIPELINE_LOG':str(log)}):
                result=start_quiz_session(self.conn,LEARNER,'lesson_cell_membranes',tutor=self.helper.tutor)
            self.assertEqual(result['status'],'awaiting_answers')
            self.assertEqual(log.read_text().splitlines(),['generation','review'])
            self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM quiz_quality_publications').fetchone()[0],1)
