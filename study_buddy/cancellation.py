"""Request-scoped cancellation for the local workbook; no automatic AI retries."""
from contextlib import contextmanager
from contextvars import ContextVar
import subprocess
from threading import Event, Lock
import time

_CURRENT = ContextVar('study_buddy_agent_work', default=None)


class AgentWorkCancelled(BaseException):
    """Control flow, deliberately outside model-failure/repair handlers."""


class WorkScope:
    def __init__(self):
        self.cancelled = Event()
        self.lock = Lock()
        self.processes = set()

    def attach(self, process):
        with self.lock:
            self.processes.add(process)
            if self.cancelled.is_set():
                if process.poll() is None:process.kill()
                raise AgentWorkCancelled()

    def detach(self, process):
        with self.lock:self.processes.discard(process)

    def cancel(self):
        with self.lock:
            self.cancelled.set()
            for process in self.processes:
                if process.poll() is None:
                    try:process.kill()
                    except ProcessLookupError:pass


class WorkRegistry:
    def __init__(self):
        self.lock = Lock()
        self.active = {}
        self.stopped = set()

    @contextmanager
    def work(self, key):
        if key is None:
            yield
            return
        scope = WorkScope()
        with self.lock:
            if key in self.stopped:scope.cancelled.set()
            self.active.setdefault(key,set()).add(scope)
        token = _CURRENT.set(scope)
        try:
            check_agent_work()
            yield
            check_agent_work()
        finally:
            _CURRENT.reset(token)
            with self.lock:
                self.active[key].discard(scope)
                if not self.active[key]:del self.active[key]

    def cancel(self, keys):
        with self.lock:
            self.stopped.update(keys)
            scopes={scope for key in keys for scope in self.active.get(key,())}
            for scope in scopes:scope.cancel()


def check_agent_work():
    scope=_CURRENT.get()
    if scope is not None and scope.cancelled.is_set():raise AgentWorkCancelled()


def track_agent_process(process):
    scope=_CURRENT.get()
    if scope is not None:scope.attach(process)


def untrack_agent_process(process):
    scope=_CURRENT.get()
    if scope is not None:scope.detach(process)


def run_agent_process(args, *, input=None, text=False, capture_output=False, cwd=None, timeout=None):
    """Keep existing subprocess behavior unless a workbook work scope is active."""
    if _CURRENT.get() is None:
        return subprocess.run(args,input=input,text=text,capture_output=capture_output,cwd=cwd,timeout=timeout)
    check_agent_work()
    process=subprocess.Popen(args,stdin=subprocess.PIPE,stdout=subprocess.PIPE if capture_output else None,
                             stderr=subprocess.PIPE if capture_output else None,text=text,cwd=cwd)
    deadline=time.monotonic()+timeout if timeout is not None else float('inf')
    try:
        track_agent_process(process)
        pending=input
        while True:
            check_agent_work()
            remaining=deadline-time.monotonic()
            if remaining<=0:raise subprocess.TimeoutExpired(args,timeout)
            try:
                stdout,stderr=process.communicate(input=pending,timeout=min(.2,remaining))
                check_agent_work()
                return subprocess.CompletedProcess(args,process.returncode,stdout,stderr)
            except subprocess.TimeoutExpired:
                pending=None
    finally:
        if process.poll() is None:process.kill()
        process.communicate()
        untrack_agent_process(process)
