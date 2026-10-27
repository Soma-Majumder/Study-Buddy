"""Serve the offline controller lab on loopback only; no application DB or providers."""
import argparse
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.offline_controller_demo import run_scenario, SCENARIOS

ROOT = Path(__file__).resolve().parent.parent


class DemoServer(HTTPServer):
    def __init__(self, port=8765):
        super().__init__(('127.0.0.1', port), DemoHandler)
        self.token = secrets.token_urlsafe(32)


class DemoHandler(BaseHTTPRequestHandler):
    def reply(self, code, payload, content_type='application/json; charset=utf-8'):
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
        self.send_response(code)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get('Host') == f'127.0.0.1:{self.server.server_port}'

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, dict(error='Open the loopback address printed by the server.'))
        assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                  '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                  '/style.css': ('style.css', 'text/css; charset=utf-8')}
        if self.path == '/api/config':
            return self.reply(200, dict(token=self.server.token, scenarios=SCENARIOS))
        if self.path not in assets:
            return self.reply(404, dict(error='Not found.'))
        name, content_type = assets[self.path]
        self.reply(200, (ROOT / 'demo' / name).read_bytes(), content_type)

    def do_POST(self):
        if not self.valid_host():
            return self.reply(403, dict(error='Loopback host required.'))
        origin = self.headers.get('Origin')
        if origin is not None and origin != f'http://127.0.0.1:{self.server.server_port}':
            return self.reply(403, dict(error='Use this local demo page to run scenarios.'))
        if not secrets.compare_digest(self.headers.get('X-Demo-Token', ''), self.server.token):
            return self.reply(403, dict(error='Reload the demo page before running a scenario.'))
        if self.path != '/api/run':
            return self.reply(404, dict(error='Not found.'))
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 1024:
                raise ValueError()
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict) or set(payload) != {'scenario'} or payload['scenario'] not in SCENARIOS:
                raise ValueError()
        except (ValueError, TypeError):
            return self.reply(400, dict(error='Choose one of the five demo scenarios.'))
        trace = []
        try:
            report = run_scenario(payload['scenario'], trace.append)
        except Exception:
            return self.reply(500, dict(error='The offline scenario did not complete. Check the local tests before retrying.'))
        self.reply(200, dict(report=report, trace=[line for line in trace if line.strip() and not line.startswith('Verified:')],
                             verified=True, database_removed=True))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error('Port must be between 1 and 65535.')
    with DemoServer(args.port) as server:
        print(f'Offline controller lab: http://127.0.0.1:{server.server_port}', flush=True)
        print('Fixture agents only. Temporary databases. Ctrl+C to stop.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print('\nOffline controller lab stopped.', flush=True)


if __name__ == '__main__':
    main()
