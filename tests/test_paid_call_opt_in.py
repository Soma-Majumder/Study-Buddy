"""Paid calls require explicit process opt-in; credentials never imply consent."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tests import test_controller as fixtures
from tests import test_quality_revision as revision_fixtures
from study_buddy.controller import run_tutor, TutorFailure, start_quiz_session, dispatch_quality_revision

ROOT=Path(__file__).resolve().parent.parent
LEARNER=fixtures.LEARNER


class PaidCallOptInTests(unittest.TestCase):
    def setUp(self):
        self.helper=fixtures.ControllerTests();self.helper.setUp();self.addCleanup(self.helper.doCleanups)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.log=Path(self.temp.name)/'unexpected-call'
        loader=Path(self.temp.name)/'block-fetch.mjs'
        loader.write_text("import {writeFile} from 'node:fs/promises'; globalThis.fetch=async()=>{await writeFile(process.env.OPT_IN_TEST_LOG,'unexpected');throw new Error('No network permitted');};")
        env=patch.dict(os.environ,{'NODE_OPTIONS':f'--import={loader}', 'ANTHROPIC_API_KEY':'fake-key',
                                   'STUDY_BUDDY_ALLOW_PAID_CLAUDE':'false','OPT_IN_TEST_LOG':str(self.log)})
        env.start();self.addCleanup(env.stop)

    def test_tutor_and_default_learner_route_stop_before_fetch(self):
        context=dict(lessonId='lesson_cell_membranes',sessionId='session_a1b2c3d4e5f6',
                     handoffId='handoff_a1b2c3d4e5f6',explanationId='expl_a1b2c3d4e5f6')
        with self.assertRaises(TutorFailure) as raised:run_tutor(context)
        self.assertEqual(raised.exception.code,'live_calls_disabled')
        result=start_quiz_session(self.helper.conn,LEARNER,'lesson_cell_membranes',
                                  request_id='paid_opt_in_test',lesson_version=2)
        self.assertEqual(result['status'],'failed');self.assertIn('calls are disabled',result['error']);self.assertFalse(self.log.exists())
        self.assertEqual(self.helper.conn.execute('SELECT COUNT(*) FROM quizzes').fetchone()[0],0)

    def test_default_content_revision_records_disabled_code_without_fetch_or_repair(self):
        helper=revision_fixtures.QualityRevisionTests();helper.setUp();self.addCleanup(helper.doCleanups)
        result=dispatch_quality_revision(helper.conn,LEARNER,helper.op)
        self.assertEqual(result['error'],'revision_live_calls_disabled')
        self.assertFalse(self.log.exists())
        self.assertEqual(helper.conn.execute('SELECT repair_count FROM handoff_repairs').fetchone()[0],0)
