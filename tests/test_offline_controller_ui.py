"""Browser API -> real offline runner -> temporary database -> safe response."""
import http.client
import json
import threading
import unittest
from unittest.mock import patch

from scripts.offline_controller_ui import DemoServer, DemoHandler


class OfflineControllerUITests(unittest.TestCase):
    def setUp(self):
        self.logs = patch.object(DemoHandler, 'log_message')
        self.logs.start()
        self.addCleanup(self.logs.stop)
        self.server = DemoServer(0)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.close_server)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'

    def close_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)

    def request(self, method, path, data=None, headers=None):
        conn = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=10)
        try:
            body = json.dumps(data) if data is not None else None
            conn.request(method, path, body=body, headers=headers or {})
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def authorized(self):
        status, _, body = self.request('GET', '/api/config')
        self.assertEqual(status, 200)
        return {'X-Demo-Token': json.loads(body)['token'], 'Origin': self.origin, 'Content-Type': 'application/json'}

    def test_each_scenario_returns_verified_state_and_cleaned_database(self):
        headers = self.authorized()
        for scenario in ('pass', 'practice', 'recovery', 'crash', 'duplicates'):
            with self.subTest(scenario=scenario):
                status, _, body = self.request('POST', '/api/run', {'scenario': scenario}, headers)
                self.assertEqual(status, 200, body)
                result = json.loads(body)
                self.assertTrue(result['verified'])
                self.assertTrue(result['database_removed'])
                self.assertEqual(result['report']['paid_calls'], 0)
                self.assertEqual(result['report']['status'], 'failed' if scenario == 'crash' else 'completed')
                self.assertNotIn(b'answer_key', body)

    def test_assets_are_allowlisted_and_browser_safety_headers_present(self):
        for path in ('/', '/app.js', '/style.css'):
            status, headers, body = self.request('GET', path)
            self.assertEqual(status, 200)
            self.assertTrue(body)
            self.assertEqual(headers['Cache-Control'], 'no-store')
            self.assertIn("frame-ancestors 'none'", headers['Content-Security-Policy'])
        for path in ('/.env', '/../db/schema.sql', '/api/unknown'):
            self.assertEqual(self.request('GET', path)[0], 404)

    def test_unknown_and_malformed_scenarios_are_rejected_before_runner(self):
        headers = self.authorized()
        with patch('scripts.offline_controller_ui.run_scenario') as run:
            for payload in ({'scenario': 'live'}, {'scenario': []}, {'scenario': 'pass', 'database': '/tmp/real.db'}, []):
                self.assertEqual(self.request('POST', '/api/run', payload, headers)[0], 400)
            run.assert_not_called()

    def test_foreign_origin_host_and_missing_token_cannot_run_scenario(self):
        headers = self.authorized()
        with patch('scripts.offline_controller_ui.run_scenario') as run:
            for invalid in ({}, dict(headers, Origin='https://example.org'), dict(headers, Host='example.org')):
                self.assertEqual(self.request('POST', '/api/run', {'scenario': 'pass'}, invalid)[0], 403)
            self.assertEqual(self.request('GET', '/', headers={'Host': 'example.org'})[0], 403)
            run.assert_not_called()

    def test_runner_failure_has_safe_error_and_no_verified_marker(self):
        headers = self.authorized()
        with patch('scripts.offline_controller_ui.run_scenario', side_effect=RuntimeError('private exception detail')):
            status, _, body = self.request('POST', '/api/run', {'scenario': 'pass'}, headers)
        self.assertEqual(status, 500)
        self.assertNotIn(b'private exception detail', body)
        self.assertNotIn('verified', json.loads(body))
