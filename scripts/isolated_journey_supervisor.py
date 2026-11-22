"""Bound the lifetime of acceptance processes started by this supervisor.

Use ``with IsolatedJourneySupervisor(timeout_seconds=...) as owner`` and start
the host, driver and meter through ``owner.start``. Pass credentials in ``env``,
never command arguments. Output goes only to explicitly opened task log files;
the supervisor does not print commands, environment values or child output.

Windows uses a private, non-inherited kill-on-close Job. A stdlib-only bootstrap
waits for assignment before starting the payload. Other platforms fail closed;
process groups alone do not establish this containment contract. This is process
lifetime ownership, not a sandbox for hostile child code.
"""

from __future__ import annotations

import ctypes
import json
import math
import os
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from ctypes import wintypes
from pathlib import Path
from typing import Any


class JourneyProcessError(RuntimeError):
    """An owned process failed or its tree could not be cleaned up."""


class _BasicLimits(ctypes.Structure):
    _fields_ = [
        ("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
        ("flags", wintypes.DWORD), ("minimum_working_set", ctypes.c_size_t),
        ("maximum_working_set", ctypes.c_size_t), ("active_limit", wintypes.DWORD),
        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD),
    ]


class _IoCounters(ctypes.Structure):
    _fields_ = [(name, ctypes.c_uint64) for name in (
        "read_operations", "write_operations", "other_operations", "read_bytes", "write_bytes", "other_bytes",
    )]


class _ExtendedLimits(ctypes.Structure):
    _fields_ = [
        ("basic", _BasicLimits), ("io", _IoCounters),
        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t),
    ]


class _Accounting(ctypes.Structure):
    _fields_ = [
        ("user_time", ctypes.c_int64), ("kernel_time", ctypes.c_int64),
        ("period_user_time", ctypes.c_int64), ("period_kernel_time", ctypes.c_int64),
        ("page_faults", wintypes.DWORD), ("total", wintypes.DWORD),
        ("active", wintypes.DWORD), ("terminated", wintypes.DWORD),
    ]


class _WindowsJob:
    def __init__(self) -> None:
        api = ctypes.WinDLL("kernel32", use_last_error=True)
        signatures = {
            "CreateJobObjectW": ([ctypes.c_void_p, wintypes.LPCWSTR], wintypes.HANDLE),
            "SetInformationJobObject": ([wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD], wintypes.BOOL),
            "AssignProcessToJobObject": ([wintypes.HANDLE, wintypes.HANDLE], wintypes.BOOL),
            "QueryInformationJobObject": (
                [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p], wintypes.BOOL,
            ),
            "TerminateJobObject": ([wintypes.HANDLE, wintypes.UINT], wintypes.BOOL),
            "OpenProcess": ([wintypes.DWORD, wintypes.BOOL, wintypes.DWORD], wintypes.HANDLE),
            "IsProcessInJob": ([wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL)], wintypes.BOOL),
            "CloseHandle": ([wintypes.HANDLE], wintypes.BOOL),
        }
        for name, (args, result) in signatures.items():
            function = getattr(api, name)
            function.argtypes, function.restype = args, result
        self._api = api
        # No name and no inheritable security attributes: only this owner holds it.
        self._handle = api.CreateJobObjectW(None, None)
        if not self._handle:
            raise JourneyProcessError("Could not create the task-owned Windows Job")
        limits = _ExtendedLimits()
        limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not api.SetInformationJobObject(self._handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
            api.CloseHandle(self._handle)
            raise JourneyProcessError("Could not configure the task-owned Windows Job")

    def assign(self, process: subprocess.Popen[bytes]) -> None:
        handle = self._api.OpenProcess(0x0101, False, process.pid)  # SET_QUOTA | TERMINATE
        if not handle:
            raise JourneyProcessError("Could not open the newly started bootstrap")
        try:
            if not self._api.AssignProcessToJobObject(self._handle, handle):
                raise JourneyProcessError("Could not assign the bootstrap to its private Windows Job")
        finally:
            self._api.CloseHandle(handle)

    def contains(self, pid: int) -> bool:
        handle = self._api.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            result = wintypes.BOOL()
            return bool(self._api.IsProcessInJob(handle, self._handle, ctypes.byref(result)) and result.value)
        finally:
            self._api.CloseHandle(handle)

    def stop(self, deadline: float) -> bool:
        try:
            if not self._api.TerminateJobObject(self._handle, 1):
                return False
            while True:
                accounting = _Accounting()
                if not self._api.QueryInformationJobObject(
                    self._handle, 1, ctypes.byref(accounting), ctypes.sizeof(accounting), None,
                ):
                    return False
                if accounting.active == 0:
                    return True
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.01)
        finally:
            self._api.CloseHandle(self._handle)


class OwnedProcess:
    """Payload status from a private bootstrap in the owner's Windows Job."""

    def __init__(self, name: str, root: subprocess.Popen[bytes], *, service: bool) -> None:
        self.name, self._root, self._service = name, root, service
        self._finished = threading.Event()
        self.returncode: int | None = None
        self._reader = threading.Thread(target=self._read_status, daemon=True)
        self._reader.start()

    @property
    def pid(self) -> int:
        """The owned bootstrap PID; a host listening PID may be its descendant."""
        return self._root.pid

    def poll(self) -> int | None:
        return self.returncode

    def _read_status(self) -> None:
        try:
            assert self._root.stderr is not None
            self.returncode = int(self._root.stderr.readline())
        except (ValueError, OSError):
            self.returncode = 125
        finally:
            self._finished.set()


class IsolatedJourneySupervisor:
    def __init__(self, *, timeout_seconds: float, cleanup_timeout_seconds: float = 5.0) -> None:
        if any(not math.isfinite(value) or value <= 0 for value in (timeout_seconds, cleanup_timeout_seconds)):
            raise ValueError("Process and cleanup deadlines must be finite and positive")
        if os.name != "nt":
            raise NotImplementedError("Isolated journey containment currently requires Windows Jobs")
        self._timeout, self._cleanup_timeout = timeout_seconds, cleanup_timeout_seconds
        self._processes: list[OwnedProcess] = []
        self._lock = threading.RLock()
        self._stopped = threading.Event()
        self._entered = self._closed = self._timed_out = False
        self._failure: str | None = None
        self._job: _WindowsJob | None = None
        self.cleanup_verified = False

    def __enter__(self) -> IsolatedJourneySupervisor:
        if self._entered or self._closed:
            raise JourneyProcessError("The supervisor is single-use")
        self._entered = True
        self._deadline = time.monotonic() + self._timeout
        self._job = _WindowsJob()
        self._watcher = threading.Thread(target=self._watch, daemon=True)
        self._watcher.start()
        return self

    @property
    def remaining_seconds(self) -> float:
        if not self._entered:
            raise JourneyProcessError("Enter the supervisor before starting work")
        return max(0.0, self._deadline - time.monotonic())

    def start(
        self, name: str, argv: Sequence[str], *, cwd: str | os.PathLike[str] | None = None,
        env: Mapping[str, str] | None = None, output_path: str | os.PathLike[str] | None = None,
        service: bool = False,
    ) -> OwnedProcess:
        """Start new work; service=True treats even a zero exit as unexpected."""
        if not name or any(process.name == name for process in self._processes):
            raise ValueError("Each owned process needs a unique nonempty name")
        if isinstance(argv, (str, bytes)) or not argv or any(not isinstance(value, str) for value in argv):
            raise ValueError("argv must be a nonempty sequence of strings; use env for credentials")
        root = None
        try:
            with self._lock:
                self.check()
                if not self._entered or self._closed:
                    raise JourneyProcessError("The supervisor is not open")
                with open(output_path or os.devnull, "ab", buffering=0) as output:
                    root = subprocess.Popen(
                        [sys.executable, "-I", "-S", str(Path(__file__).resolve()), "--owned-child"],
                        stdin=subprocess.PIPE, stdout=output, stderr=subprocess.PIPE,
                        env=dict(env) if env is not None else None,
                        creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True,
                    )
                if self._job is not None:
                    self._job.assign(root)
                process = OwnedProcess(name, root, service=service)
                self._processes.append(process)
            # A blocked startup pipe must not keep the watchdog from stopping its Job.
            assert root.stdin is not None
            root.stdin.write(json.dumps({"argv": list(argv), "cwd": os.fspath(cwd) if cwd else None}).encode() + b"\n")
            root.stdin.flush()
            self.check()
            return process
        except BaseException as error:
            bootstrap_stopped = True
            try:
                if root is not None and not any(process._root is root for process in self._processes):
                    # Before the gate opens only our bootstrap can exist.
                    root.kill()
                    root.wait(timeout=self._cleanup_timeout)
                    for stream in (root.stdin, root.stderr):
                        if stream is not None:
                            stream.close()
            except (OSError, subprocess.TimeoutExpired):
                bootstrap_stopped = False
            finally:
                self.close()
                self.cleanup_verified = self.cleanup_verified and bootstrap_stopped
            if isinstance(error, (KeyboardInterrupt, SystemExit)):
                raise
            self.check()
            raise JourneyProcessError("Could not start task-owned work") from None

    def owns_pid(self, pid: int) -> bool:
        """Read-only membership check, e.g. before contacting a listening host."""
        with self._lock:
            if self._closed or pid <= 0:
                return False
            return self._job is not None and self._job.contains(pid)

    def check(self) -> None:
        if self._timed_out:
            raise TimeoutError("The isolated journey deadline expired")
        if self._failure:
            raise JourneyProcessError(self._failure)

    def wait(self, process: OwnedProcess) -> int:
        if process not in self._processes:
            raise ValueError("Cannot wait on a process this supervisor did not start")
        while not process._finished.wait(0.02):
            self.check()
        self.check()
        assert process.returncode is not None
        if process.returncode or process._service:
            self._failure = f"Owned process {process.name!r} failed with exit code {process.returncode}"
            self.close()
            raise JourneyProcessError(self._failure)
        return process.returncode

    def _watch(self) -> None:
        while not self._stopped.wait(0.02):
            if self.remaining_seconds == 0:
                self._timed_out = True
                self.close()
                return
            failed = next((process for process in self._processes
                           if process.poll() is not None and (process.returncode != 0 or process._service)), None)
            if failed:
                self._failure = f"Owned process {failed.name!r} exited unexpectedly with exit code {failed.returncode}"
                self.close()
                return

    def close(self) -> bool:
        with self._lock:
            if self._closed:
                return self.cleanup_verified
            self._closed = True
            self._stopped.set()
            deadline = time.monotonic() + self._cleanup_timeout
            if self._job is not None:
                clean = self._job.stop(deadline)
            else:
                clean = not self._processes
            for process in self._processes:
                try:
                    process._root.wait(timeout=max(0.001, deadline - time.monotonic()))
                except subprocess.TimeoutExpired:
                    clean = False
                    # Closing a pipe while its reader/writer is blocked can wait
                    # indefinitely. Unknown root lifetime is an unverified result.
                    continue
                process._reader.join(timeout=max(0.0, deadline - time.monotonic()))
                if process._reader.is_alive():
                    clean = False
                    continue
                for stream in (process._root.stdin, process._root.stderr):
                    if stream is not None:
                        stream.close()
            self.cleanup_verified = clean
            return clean

    def __exit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        if not self.close():
            raise JourneyProcessError("Task-owned process cleanup could not be verified")
        if exc_type is None:
            self.check()


def _owned_child() -> int:
    """Internal gate; payload output never enters the status pipe."""
    if os.name != "nt":
        return 125
    try:
        request = json.loads(sys.stdin.buffer.readline())
        payload = subprocess.Popen(request["argv"], cwd=request["cwd"], stdin=subprocess.DEVNULL, stderr=subprocess.STDOUT)
        print(payload.wait(), file=sys.stderr, flush=True)
    except Exception:
        return 125
    return 0


if __name__ == "__main__":
    raise SystemExit(_owned_child() if sys.argv[1:] == ["--owned-child"] else 2)
