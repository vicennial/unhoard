"""Run a helper program in its own process group, so a timeout or Ctrl-C ends it and everything it started.

``subprocess.run(timeout=...)`` kills only the direct child. Agents start MCP servers, shells and other
helpers; killing just the parent leaves those running (and, for a generated MCP config, still holding secrets).

Process groups are a POSIX feature. Where they are missing (Windows) this module degrades to killing the direct
child only, instead of failing with an ``AttributeError``.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import threading
from typing import Any, Iterator, Mapping, Sequence

POSIX = os.name == "posix"
# Popen options that put the child in its own session (and so its own process group)
NEW_SESSION: dict[str, Any] = {"start_new_session": True} if POSIX else {}
# signals whose default action ends this process without running ``finally`` blocks
_STOP_SIGNALS = tuple(getattr(signal, name) for name in ("SIGTERM", "SIGHUP") if hasattr(signal, name))


def kill_group(proc: subprocess.Popen) -> None:
    """SIGKILL the whole process group of ``proc`` (it was started with ``NEW_SESSION``)."""
    killpg = getattr(os, "killpg", None)
    sigkill = getattr(signal, "SIGKILL", None)
    if killpg is not None and sigkill is not None:
        try:
            killpg(proc.pid, sigkill)
            return
        except (ProcessLookupError, PermissionError, OSError):
            pass
    try:
        proc.kill()
    except OSError:
        pass


def _close(proc: subprocess.Popen) -> None:
    for stream in (proc.stdin, proc.stdout, proc.stderr):
        try:
            if stream:
                stream.close()
        except OSError:
            pass


@contextlib.contextmanager
def exit_on_stop_signals() -> Iterator[None]:
    """While active, SIGTERM and SIGHUP raise ``SystemExit`` instead of ending the process at once, so a
    supervisor's kill unwinds through ``run_group`` and kills the agent's group too. Handlers are restored
    afterwards. A signal that is ignored when this starts (``nohup`` ignores SIGHUP) stays ignored: the user chose
    that. Only the main thread can install handlers; elsewhere (and where a signal does not exist) this does
    nothing."""
    previous: dict[int, Any] = {}

    def handler(signum: int, frame: Any) -> None:
        raise SystemExit(128 + signum)

    if threading.current_thread() is threading.main_thread():
        for signum in _STOP_SIGNALS:
            try:
                if signal.getsignal(signum) == signal.SIG_IGN:
                    continue
                previous[signum] = signal.signal(signum, handler)
            except (ValueError, OSError):
                pass
    try:
        yield
    finally:
        for signum, old in previous.items():
            try:
                signal.signal(signum, old if old is not None else signal.SIG_DFL)
            except (ValueError, OSError):
                pass


def run_group(argv: Sequence[str], input_text: str | None = None, timeout: float | None = None,
              cwd: str | None = None, env: Mapping[str, str] | None = None) -> subprocess.CompletedProcess:
    """Like ``subprocess.run(capture_output=True, text=True)``, but in a new session; the whole group dies on
    timeout, interruption, SIGTERM or SIGHUP. Raises ``subprocess.TimeoutExpired`` / ``FileNotFoundError`` like
    ``run``. After a timeout this never waits for the pipes to close: a helper that left the group (``setsid``)
    can hold them open, so the wait is for the direct child only."""
    with exit_on_stop_signals():
        proc = subprocess.Popen(list(argv), stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, errors="replace", cwd=cwd,
                                env=None if env is None else dict(env), **NEW_SESSION)
        try:
            out, err = proc.communicate(input_text, timeout=timeout)
        except BaseException:  # a timeout, Ctrl-C, SIGTERM/SIGHUP (as SystemExit) or any error
            kill_group(proc)
            _finish(proc)
            raise
    return subprocess.CompletedProcess(list(argv), proc.returncode, out, err)


def _finish(proc: subprocess.Popen) -> None:
    """Reap the direct child (bounded: it was just killed) and close the pipes without reading them."""
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(OSError):
            proc.kill()
    _close(proc)
