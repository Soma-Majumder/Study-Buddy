"""Gate failures must be visible, redacted, and independent of Git's parent root."""
from pathlib import Path
import tempfile
import io
import unittest
from unittest.mock import patch
import importlib.metadata

from scripts.check_backend_local import scan_sources, dependency_mismatches, child_environment, node_test_summary, run_python_tests


class BackendLocalCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)

    def test_secret_pattern_reports_only_location_and_code(self):
        secret='sk-ant-'+'x'*60
        (self.root/'accidental.py').write_text('key='+secret)
        findings=scan_sources(self.root)
        self.assertEqual(findings,[{'path':'accidental.py','code':'credential_pattern'}])
        self.assertNotIn(secret,str(findings))

    def test_env_files_fail_but_template_and_fixture_keys_are_allowed(self):
        (self.root/'.env').write_text('placeholder')
        (self.root/'.env.example').write_text('ANTHROPIC_API_KEY=replace-me')
        (self.root/'fixture.json').write_text('{"answer_key":["a"]}')
        self.assertEqual(scan_sources(self.root),[{'path':'.env','code':'environment_file_in_checkpoint'}])

    def test_dependency_trees_and_report_logs_are_not_scanned(self):
        for directory in ['node_modules','.local-checks']:
            path=self.root/directory;path.mkdir();(path/'sample').write_text('sk-ant-'+'x'*60)
        self.assertEqual(scan_sources(self.root),[])

    def test_source_symlink_is_reported_instead_of_followed(self):
        (self.root/'link').symlink_to('/etc/hosts')
        self.assertEqual(scan_sources(self.root),[{'path':'link','code':'unscanned_symlink'}])

    def lock(self,text):
        path=self.root/'requirements.lock';path.write_text(text);return path

    def test_pinned_dependency_must_match_and_missing_is_a_failure(self):
        path=self.lock('demo==1.0 --hash=sha256:'+'a'*64+'\n')
        self.assertEqual(dependency_mismatches(path,lambda _: '1.0'),[])
        self.assertEqual(dependency_mismatches(path,lambda _: '2.0'),['version_mismatch:demo'])
        def missing(_):raise importlib.metadata.PackageNotFoundError()
        self.assertEqual(dependency_mismatches(path,missing),['missing_package:demo'])

    def test_empty_unhashed_and_duplicate_lock_entries_fail(self):
        self.assertEqual(dependency_mismatches(self.lock('')),['empty_lock'])
        self.assertEqual(dependency_mismatches(self.lock('demo>=1')),['invalid_lock_entry'])
        row='demo==1 --hash=sha256:'+'a'*64+'\n'
        self.assertEqual(dependency_mismatches(self.lock(row+row),lambda _:'1'),['duplicate_lock_entry'])

    def test_child_environment_removes_credentials_and_disables_live_calls(self):
        with patch.dict('os.environ',{'ANTHROPIC_API_KEY':'private-value','OPENAI_API_KEY':'private-value','NODE_OPTIONS':'untrusted-preload','STUDY_BUDDY_ALLOW_PAID_CLAUDE':'true'}):
            env=child_environment()
        for key in ('ANTHROPIC_API_KEY','OPENAI_API_KEY','NODE_OPTIONS'):self.assertNotIn(key,env)
        self.assertEqual(env['STUDY_BUDDY_ALLOW_PAID_CLAUDE'],'false')

    def tap(self,tests=2,passed=2,skipped=0):
        return f'# tests {tests}\n# pass {passed}\n# fail 0\n# skipped {skipped}\n# cancelled 0\n# todo 0\n'

    def test_node_empty_skipped_or_incomplete_reports_do_not_pass(self):
        self.assertTrue(node_test_summary(self.tap()))
        for output in ['',self.tap(0,0),self.tap(2,1,1),self.tap().replace('# fail 0','# fail 1')]:
            self.assertFalse(node_test_summary(output))

    def test_python_gate_does_not_accept_zero_tests_or_skips(self):
        with patch('scripts.check_backend_local.unittest.defaultTestLoader.discover',return_value=unittest.TestSuite()):
            self.assertFalse(run_python_tests(self.root,stream=io.StringIO()))
        class Skipped(unittest.TestCase):
            @unittest.skip('fixture skip')
            def test_sample(self):pass
        suite=unittest.defaultTestLoader.loadTestsFromTestCase(Skipped)
        with patch('scripts.check_backend_local.unittest.defaultTestLoader.discover',return_value=suite):
            self.assertFalse(run_python_tests(self.root,stream=io.StringIO()))

    def test_main_stops_at_first_failed_command_and_records_failure(self):
        import json
        from scripts import check_backend_local as gate
        with patch.object(gate,'ROOT',self.root), patch('sys.argv',['check_backend_local.py']), \
             patch.object(gate,'dependency_mismatches',return_value=[]), \
             patch.object(gate,'run_command',return_value=False) as run:
            self.assertEqual(gate.main(),1)
        run.assert_called_once()
        report=json.loads((self.root/'.local-checks/report.json').read_text())
        self.assertEqual(report['checks'][-1]['name'],'Python dependency health')
        self.assertFalse(report['checks'][-1]['passed'])
