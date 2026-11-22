"""Offline process-tree lifetime proof; no host, provider or network is started."""

from __future__ import annotations

import ctypes
import json
import os
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import isolated_journey_supervisor as supervisor

WINDOWS = pytest.mark.skipif(os.name != "nt", reason="Windows Job containment")

PAYLOAD = '''
import json, os, subprocess, sys, time
from pathlib import Path
depth, prefix, mode = int(sys.argv[1]), sys.argv[2], sys.argv[3]
if depth:
    subprocess.Popen([sys.executable, __file__, str(depth - 1), prefix, 'stay'])
Path(prefix + '.' + str(depth)).write_text(str(os.getpid()))
if mode != 'stay':
    end = time.monotonic() + 8
    while not Path(prefix + '.0').exists() and time.monotonic() < end:
        time.sleep(.01)
    raise SystemExit(0 if mode == 'success' else 7)
time.sleep(15)
'''


@pytest.fixture
def payload(tmp_path):
    path = tmp_path / "child.py"
    path.write_text(PAYLOAD, encoding="utf-8")
    return path


def _alive(pid: int) -> bool:
    if os.name == "nt":
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        api.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        api.OpenProcess.restype = wintypes.HANDLE
        api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        api.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = api.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            return bool(api.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259
        finally:
            api.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    stat = Path(f"/proc/{pid}/stat")
    return not stat.exists() or stat.read_text().rpartition(")")[2].split()[0] != "Z"


def _pids(prefix: Path, *, depth: int = 2) -> list[int]:
    files = [Path(str(prefix) + f".{level}") for level in range(depth + 1)]
    deadline = time.monotonic() + 8
    while not all(path.exists() and path.read_text() for path in files):
        assert time.monotonic() < deadline, "Offline children did not report their identities"
        time.sleep(0.01)
    return [int(path.read_text()) for path in files]


def _stopped(pids: list[int]) -> None:
    deadline = time.monotonic() + 3
    while any(_alive(pid) for pid in pids):
        assert time.monotonic() < deadline, f"Task-owned children still alive: {pids}"
        time.sleep(0.01)


def _tree(owner, payload, prefix, mode="stay"):
    return owner.start("tree", [sys.executable, str(payload), "2", str(prefix), mode])


@WINDOWS
def test_success_cleans_descendants_after_the_payload_parent_exits(payload, tmp_path):
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(15)"],
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    owner = supervisor.IsolatedJourneySupervisor(timeout_seconds=10)
    try:
        with owner:
            process = _tree(owner, payload, tmp_path / "tree", "success")
            pids = _pids(tmp_path / "tree")
            assert owner.wait(process) == 0
            assert _alive(pids[0]) and _alive(pids[1])
            assert all(owner.owns_pid(pid) for pid in pids[:2])
            assert not owner.owns_pid(unrelated.pid)
            assert not owner.owns_pid(os.getpid())
        _stopped([*pids, process.pid])
        assert owner.cleanup_verified
        assert owner.close()
        assert unrelated.poll() is None
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=3)


@pytest.mark.parametrize("error", [RuntimeError, KeyboardInterrupt])
@WINDOWS
def test_exception_or_interruption_cleans_all_owned_generations(error, payload, tmp_path):
    owner = supervisor.IsolatedJourneySupervisor(timeout_seconds=10)
    with pytest.raises(error):
        with owner:
            process = _tree(owner, payload, tmp_path / "tree")
            pids = _pids(tmp_path / "tree")
            raise error("offline interruption")
    _stopped([*pids, process.pid])
    assert owner.cleanup_verified


@WINDOWS
def test_payload_failure_stops_the_other_owned_tree(payload, tmp_path):
    owner = supervisor.IsolatedJourneySupervisor(timeout_seconds=10)
    with pytest.raises(supervisor.JourneyProcessError, match="exit code 7"):
        with owner:
            tree = _tree(owner, payload, tmp_path / "tree")
            pids = _pids(tmp_path / "tree")
            failure = owner.start("driver", [sys.executable, "-c", "raise SystemExit(7)"])
            owner.wait(failure)
    _stopped([*pids, tree.pid, failure.pid])
    assert owner.cleanup_verified


@WINDOWS
def test_watchdog_deadline_stops_trees_without_wait_or_health_polling(payload, tmp_path):
    owner = supervisor.IsolatedJourneySupervisor(timeout_seconds=10)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline expired"):
        with owner:
            process = _tree(owner, payload, tmp_path / "tree")
            pids = _pids(tmp_path / "tree")
            # Deliberately do not call owner.wait/check while its watchdog runs.
            deadline = started + 13
            while not owner.cleanup_verified:
                assert time.monotonic() < deadline
                time.sleep(0.02)
            _stopped([*pids, process.pid])
    assert time.monotonic() - started < 13


@WINDOWS
def test_abrupt_supervisor_death_cleans_its_descendants(payload, tmp_path):
    controller = tmp_path / "controller.py"
    repo = str(Path(__file__).resolve().parents[1])
    controller.write_text(
        "import sys, time\nfrom pathlib import Path\n"
        f"sys.path.insert(0, {repo!r})\n"
        "from scripts.isolated_journey_supervisor import IsolatedJourneySupervisor\n"
        "with IsolatedJourneySupervisor(timeout_seconds=10) as owner:\n"
        f"    process = owner.start('tree', [sys.executable, {str(payload)!r}, '2', {str(tmp_path / 'tree')!r}, 'stay'])\n"
        f"    Path({str(tmp_path / 'bootstrap')!r}).write_text(str(process.pid))\n"
        "    time.sleep(15)\n", encoding="utf-8",
    )
    parent = subprocess.Popen([sys.executable, str(controller)], stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        pids = _pids(tmp_path / "tree")
        bootstrap = int((tmp_path / "bootstrap").read_text())
        parent.kill()
        parent.wait(timeout=3)
        _stopped([*pids, bootstrap])
    finally:
        if parent.poll() is None:
            parent.kill()
            parent.wait(timeout=3)


@WINDOWS
def test_logs_and_credentials_use_private_handles_and_environment(tmp_path, capfd):
    script = tmp_path / "env_child.py"
    evidence = tmp_path / "child.json"
    script.write_text(
        "import json, os, sys\nfrom pathlib import Path\n"
        f"Path({str(evidence)!r}).write_text(json.dumps([sys.argv, os.environ['JOURNEY_CANARY'], os.getpid()]))\n"
        "print('owned child output', flush=True)\n", encoding="utf-8",
    )
    log = tmp_path / "owned.log"
    with supervisor.IsolatedJourneySupervisor(timeout_seconds=5) as owner:
        process = owner.start("environment", [sys.executable, str(script)],
                              env={**os.environ, "JOURNEY_CANARY": "environment-only-canary"}, output_path=log)
        assert owner.wait(process) == 0
    argv, value, payload_pid = json.loads(evidence.read_text())
    assert value == "environment-only-canary" and value not in str(argv)
    assert owner.cleanup_verified
    _stopped([process.pid, payload_pid])
    assert process._root.stdin.closed and process._root.stderr.closed
    assert log.read_text().strip() == "owned child output"
    assert capfd.readouterr() == ("", "")
    # Job accounting and independent PID checks establish exit. Windows file
    # sharing/antivirus cleanup need not release a rename at the same instant.
    deadline = time.monotonic() + 3
    while True:
        try:
            log.rename(tmp_path / "closed.log")
            break
        except PermissionError:
            assert time.monotonic() < deadline, "Task log was not released after process cleanup"
            time.sleep(.01)


@pytest.mark.skipif(os.name != "nt", reason="Windows Job admission")
def test_job_assignment_failure_never_runs_payload(monkeypatch, tmp_path):
    marker = tmp_path / "should-not-exist"
    roots = []
    original = subprocess.Popen

    def capture(*args, **kwargs):
        process = original(*args, **kwargs)
        roots.append(process)
        return process

    def refuse(*args):
        raise supervisor.JourneyProcessError("offline job refusal")

    monkeypatch.setattr(supervisor.subprocess, "Popen", capture)
    monkeypatch.setattr(supervisor._WindowsJob, "assign", refuse)
    owner = supervisor.IsolatedJourneySupervisor(timeout_seconds=5)
    with pytest.raises(supervisor.JourneyProcessError, match="Could not start task-owned work"):
        with owner:
            owner.start("refused", [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"])
    assert not marker.exists()
    _stopped([process.pid for process in roots])
    assert owner.cleanup_verified


@WINDOWS
def test_cannot_attach_shell_text_or_wait_on_foreign_work():
    with supervisor.IsolatedJourneySupervisor(timeout_seconds=5) as owner:
        with pytest.raises(ValueError, match="sequence of strings"):
            owner.start("bad", "python -c 'pass'")
        with pytest.raises(ValueError, match="did not start"):
            owner.wait(object())
        assert not owner.owns_pid(os.getpid())


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan")])
def test_deadline_must_be_finite_and_positive(timeout):
    with pytest.raises(ValueError, match="finite and positive"):
        supervisor.IsolatedJourneySupervisor(timeout_seconds=timeout)


def test_unsupported_platform_fails_before_starting_work(monkeypatch):
    monkeypatch.setattr(supervisor, "os", SimpleNamespace(name="posix"))
    with pytest.raises(NotImplementedError, match="requires Windows Jobs"):
        supervisor.IsolatedJourneySupervisor(timeout_seconds=5)
    assert supervisor._owned_child() == 125


@WINDOWS
@pytest.mark.parametrize("exitcode", [0, 7])
def test_service_exit_always_stops_the_other_owned_tree(exitcode, payload, tmp_path):
    owner = supervisor.IsolatedJourneySupervisor(timeout_seconds=10)
    with pytest.raises(supervisor.JourneyProcessError, match=f"exit code {exitcode}"):
        with owner:
            tree = _tree(owner, payload, tmp_path / "tree")
            pids = _pids(tmp_path / "tree")
            service = owner.start("host", [sys.executable, "-c", f"raise SystemExit({exitcode})"], service=True)
            deadline = time.monotonic() + 3
            while not owner.cleanup_verified:
                assert time.monotonic() < deadline
                time.sleep(.02)
    _stopped([*pids, tree.pid, service.pid])


@WINDOWS
def test_blocked_startup_pipe_does_not_prevent_deadline_cleanup(monkeypatch):
    roots = []
    original = subprocess.Popen

    def unresponsive_bootstrap(argv, **kwargs):
        process = original([sys.executable, "-I", "-S", "-c", "import time; time.sleep(15)"], **kwargs)
        roots.append(process)
        return process

    monkeypatch.setattr(supervisor.subprocess, "Popen", unresponsive_bootstrap)
    owner = supervisor.IsolatedJourneySupervisor(timeout_seconds=1)
    started = time.monotonic()
    with pytest.raises(TimeoutError, match="deadline expired"):
        with owner:
            owner.start("blocked", [sys.executable, "-c", "pass", "x" * 200_000])
    assert time.monotonic() - started < 6
    assert owner.cleanup_verified
    _stopped([process.pid for process in roots])


@WINDOWS
def test_cleanup_cannot_be_reported_verified_when_job_stop_reports_failure(monkeypatch, payload, tmp_path):
    original = supervisor._WindowsJob.stop

    def unconfirmed(job, deadline):
        assert original(job, deadline)
        return False

    monkeypatch.setattr(supervisor._WindowsJob, "stop", unconfirmed)
    owner = supervisor.IsolatedJourneySupervisor(timeout_seconds=10)
    with pytest.raises(supervisor.JourneyProcessError, match="cleanup could not be verified"):
        with owner:
            tree = _tree(owner, payload, tmp_path / "tree")
            pids = _pids(tmp_path / "tree")
    assert not owner.cleanup_verified
    _stopped([*pids, tree.pid])


@WINDOWS
def test_unconfirmed_root_exit_never_closes_potentially_blocked_status_pipes():
    class BlockedPipe:
        def close(self):
            raise AssertionError("Closing an unconfirmed root's pipe can block")

    class UnconfirmedRoot:
        stdin = stderr = BlockedPipe()

        def wait(self, *, timeout):
            raise subprocess.TimeoutExpired("owned bootstrap", timeout)

    owner = supervisor.IsolatedJourneySupervisor(timeout_seconds=5, cleanup_timeout_seconds=.1)
    owner._job = SimpleNamespace(stop=lambda deadline: True)
    owner._processes = [SimpleNamespace(_root=UnconfirmedRoot())]
    started = time.monotonic()
    assert not owner.close()
    assert not owner.cleanup_verified
    assert time.monotonic() - started < 1


@WINDOWS
def test_unconfirmed_pre_gate_bootstrap_cannot_be_reported_clean(monkeypatch):
    class UnconfirmedBootstrap:
        stdin = stderr = None

        def kill(self):
            pass

        def wait(self, *, timeout):
            raise subprocess.TimeoutExpired("owned bootstrap", timeout)

    def refuse(*args):
        raise supervisor.JourneyProcessError("offline assignment refusal")

    monkeypatch.setattr(supervisor.subprocess, "Popen", lambda *args, **kwargs: UnconfirmedBootstrap())
    monkeypatch.setattr(supervisor._WindowsJob, "assign", refuse)
    owner = supervisor.IsolatedJourneySupervisor(timeout_seconds=5)
    with pytest.raises(supervisor.JourneyProcessError, match="cleanup could not be verified"):
        with owner:
            owner.start("refused", [sys.executable, "-c", "pass"])
    assert not owner.cleanup_verified
