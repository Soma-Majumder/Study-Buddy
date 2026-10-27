"""Offline menu exercises real controller state while forbidding provider processes."""
from contextlib import ExitStack
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from scripts import offline_controller_demo as demo


class OfflineControllerDemoTests(unittest.TestCase):
    def test_all_scenarios_use_no_provider_process_or_network_even_with_opt_in(self):
        with ExitStack() as stack:
            stack.enter_context(patch.dict(os.environ, {'STUDY_BUDDY_ALLOW_PAID_CLAUDE': 'true'}))
            process = stack.enter_context(patch('subprocess.Popen', side_effect=AssertionError('No provider processes')))
            network = stack.enter_context(patch('socket.socket.connect', side_effect=AssertionError('No network')))
            for name in demo.SCENARIOS:
                with self.subTest(scenario=name):
                    output = []
                    report = demo.run_scenario(name, output.append)
                    self.assertEqual(report['paid_calls'], 0)
                    self.assertEqual(report['status'], 'failed' if name == 'crash' else 'completed')
                    self.assertEqual(report['fixture_calls']['tutor'], 2 if name == 'practice' else 0 if name == 'crash' else 1)
                    self.assertIn('Disposable demo database removed.', '\n'.join(output))
            process.assert_not_called()
            network.assert_not_called()

    def test_menu_rejects_bad_input_then_runs_choice_and_quits(self):
        answers = iter(['nope', '1', 'q'])
        output = []
        self.assertEqual(demo.main([], input_fn=lambda prompt: next(answers), emit=output.append), 0)
        text = '\n'.join(output)
        self.assertIn('Choose 1–5, a, or q.', text)
        self.assertIn('"scores": [8]', text)
        self.assertIn('Demo closed.', text)

    def test_menu_eof_exits_cleanly(self):
        def eof(prompt):
            raise EOFError
        self.assertEqual(demo.main([], input_fn=eof, emit=lambda text: None), 0)

    def test_menu_run_all_executes_each_scenario(self):
        answers = iter(['a', 'q'])
        with patch.object(demo, 'run_scenario') as run:
            demo.main([], input_fn=lambda prompt: next(answers), emit=lambda text: None)
        self.assertEqual([call.args[0] for call in run.call_args_list], list(demo.SCENARIOS))

    def test_temporary_database_is_removed_on_unexpected_failure(self):
        real_temp = tempfile.TemporaryDirectory
        created = []
        def tracked(*args, **kwargs):
            directory = real_temp(*args, **kwargs)
            created.append(Path(directory.name))
            return directory
        with patch.object(demo.tempfile, 'TemporaryDirectory', side_effect=tracked), \
                patch.object(demo.OfflineAgents, 'tutor', side_effect=RuntimeError('fixture failure')):
            with self.assertRaises(RuntimeError):
                demo.run_scenario('pass', lambda text: None)
        self.assertTrue(created)
        self.assertTrue(all(not directory.exists() for directory in created))

    def test_cli_runs_from_another_directory_without_loading_env(self):
        script = Path(demo.__file__).resolve()
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([sys.executable, '-B', str(script), '--scenario', 'all'],
                cwd=directory, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.count('Verified:'), 5)
        self.assertNotIn('correct_option', result.stdout)
        self.assertNotIn('answer_key', result.stdout)
