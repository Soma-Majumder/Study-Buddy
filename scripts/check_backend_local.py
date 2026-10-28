"""Local backend gate: no installs, Git writes, provider calls or online audits."""
import argparse
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
IGNORED = {'.git', 'node_modules', '__pycache__', '.venv', '.local-checks'}
PATTERNS = (
    re.compile(rb'\bsk-ant-[A-Za-z0-9_-]{40,}'),
    re.compile(rb'\bsk-proj-[A-Za-z0-9_-]{40,}'),
    re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{36,}'),
    re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
)


def scan_sources(root):
    """Narrow credential-pattern scan. Return locations/codes, never secret text."""
    findings = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in list(dirs):
            path = Path(directory)/name
            if name in IGNORED:
                dirs.remove(name)
            elif path.is_symlink():
                findings.append({'path':str(path.relative_to(root)), 'code':'unscanned_symlink'})
                dirs.remove(name)
        for name in files:
            path = Path(directory)/name
            relative = path.relative_to(root).as_posix()
            if relative.startswith('backend/evals/results/'):
                continue
            if path.is_symlink():
                findings.append({'path':relative, 'code':'unscanned_symlink'})
                continue
            if name == '.env' or name.startswith('.env.') and name != '.env.example':
                findings.append({'path':relative, 'code':'environment_file_in_checkpoint'})
                continue
            if path.stat().st_size > 32*1024*1024:
                findings.append({'path':relative, 'code':'unscanned_large_file'})
                continue
            if any(pattern.search(path.read_bytes()) for pattern in PATTERNS):
                findings.append({'path':relative, 'code':'credential_pattern'})
    return findings


def dependency_mismatches(lock, version=importlib.metadata.version):
    rows = [line.strip() for line in lock.read_text().splitlines() if line.strip() and not line.startswith('#')]
    problems = []
    if not rows:
        return ['empty_lock']
    seen = set()
    for row in rows:
        match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:[a-f0-9]{64}', row)
        if not match:
            problems.append('invalid_lock_entry');continue
        package, expected = match.groups()
        normalized = re.sub('[-_.]+','-',package).lower()
        if normalized in seen:
            problems.append('duplicate_lock_entry');continue
        seen.add(normalized)
        try:
            if version(package) != expected:problems.append('version_mismatch:'+package)
        except importlib.metadata.PackageNotFoundError:
            problems.append('missing_package:'+package)
    return problems


def child_environment():
    env = os.environ.copy()
    for name in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','OPENAI_API_KEY','NODE_OPTIONS',
                 'STUDY_BUDDY_EVAL_CAPTURE_PATH','PYTHONPATH','PYTHONHOME'):
        env.pop(name,None)
    env['STUDY_BUDDY_ALLOW_PAID_CLAUDE'] = 'false'
    env['PYTHONDONTWRITEBYTECODE'] = '1'
    return env


def run_python_tests(root, stream=None):
    suite = unittest.defaultTestLoader.discover(str(root/'tests'),top_level_dir=str(root))
    result = unittest.TextTestRunner(stream=stream,verbosity=1).run(suite)
    return result.wasSuccessful() and result.testsRun > 0 and not result.skipped


def node_test_summary(output):
    values = {}
    for key in ('tests','pass','fail','skipped','cancelled','todo'):
        match = re.search(r'^# '+key+r' (\d+)\s*$',output,re.M)
        if not match:return False
        values[key]=int(match[1])
    return values['tests'] > 0 and values['pass']==values['tests'] and all(values[k]==0 for k in ('fail','skipped','cancelled','todo'))


def run_command(command, cwd, env, log, timeout=180):
    try:
        with log.open('w') as output:
            run = subprocess.run(command,cwd=cwd,env=env,stdout=output,stderr=subprocess.STDOUT,timeout=timeout)
        return run.returncode == 0
    except (OSError,subprocess.TimeoutExpired):
        return False


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--python-tests',action='store_true',help=argparse.SUPPRESS)
    args=parser.parse_args()
    if args.python_tests:
        os.environ.update(child_environment())
        # Remove sensitive/inherited variables, not just overwrite present keys.
        for name in ('ANTHROPIC_API_KEY','ANTHROPIC_AUTH_TOKEN','OPENAI_API_KEY','NODE_OPTIONS','PYTHONPATH','PYTHONHOME'):
            os.environ.pop(name,None)
        return 0 if run_python_tests(ROOT) else 1
    folder=ROOT/'.local-checks';folder.mkdir(mode=0o700,exist_ok=True)
    report={'paid_calls_enabled':False,'checks':[]}
    def record(name,ok,details=None):
        report['checks'].append({'name':name,'passed':bool(ok),'details':details})
        (folder/'report.json').write_text(json.dumps(report,indent=2)+'\n')
        print(('PASS' if ok else 'FAIL')+' | '+name,flush=True)
        return ok
    env=child_environment()
    findings=scan_sources(ROOT)
    if not record('source credential-pattern check',not findings,findings):return 1
    mismatches=dependency_mismatches(ROOT/'requirements-backend.lock')
    if not record('Python locked versions',not mismatches,mismatches):return 1
    if not record('Python dependency health',run_command([sys.executable,'-m','pip','check'],ROOT,env,folder/'pip.txt')):return 1
    node=os.environ.get('STUDY_BUDDY_NODE') or shutil.which('node')
    if not record('Node runtime available',bool(node and Path(node).is_file())):return 1
    env['STUDY_BUDDY_NODE']=node
    if not record('Python tests (no empty/skipped run)',run_command([sys.executable,'-B',str(Path(__file__).resolve()),'--python-tests'],ROOT,env,folder/'python-tests.txt')):return 1
    files=sorted(p.name for p in (ROOT/'backend').glob('*.test.mjs'))
    ok=bool(files) and run_command([node,'--test','--test-reporter=tap',*files],ROOT/'backend',env,folder/'node-tests.txt')
    ok=ok and node_test_summary((folder/'node-tests.txt').read_text())
    if not record('JavaScript tests (no empty/skipped run)',ok):return 1
    if not record('Offline pass/retry/blocked scenarios',run_command([sys.executable,'-B','scripts/check_backend_offline.py'],ROOT,env,folder/'scenarios.txt')):return 1
    print('Backend local gate passed. Details: .local-checks/report.json',flush=True)
    print('Checks contracts and mechanics, not factual accuracy or full security.',flush=True)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
