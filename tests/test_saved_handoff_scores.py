import json
from pathlib import Path
import tempfile
import unittest
from scripts.score_saved_handoffs import extract, ROOT


class SavedHandoffScoreTests(unittest.TestCase):
    def extract(self,log):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'run.json';p.write_text(json.dumps(log));return extract(p)

    def test_failed_tutor_is_not_accepted(self):
        rows,_=self.extract(dict(stages=[dict(stage='tutor',status='failed',live=True)]))
        self.assertFalse(rows[0]['accepted'])
        self.assertEqual(rows[0]['run_type'],'live')

    def test_missing_route_and_dispatch_never_become_feedback_handoff_success(self):
        rows,missing=self.extract(dict(stages=[dict(stage='feedback_first',status='returned',output={})]))
        self.assertEqual(rows,[])
        self.assertIsNone(missing[0]['accepted'])

    def test_reused_content_is_separate_and_repair_count_preserved(self):
        h=json.loads((ROOT/'schemas/v1/examples/tutor_to_assessment.example.json').read_text())
        rows,_=self.extract(dict(stages=[dict(stage='reused_live_tutor_output',status='reused',output=dict(handoff=h,repairCount=1)),dict(stage='assessment',status='valid')]))
        self.assertTrue(rows[0]['accepted'])
        self.assertEqual(rows[0]['run_type'],'replay')
        self.assertEqual(rows[0]['technical_repairs'],1)
