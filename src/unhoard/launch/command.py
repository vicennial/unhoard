"""Adapter that spawns a configured program with the brief on its stdin, detached."""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Mapping

from .. import procs
from ..config import ENV_CLIENT_SECRET, ENV_TOKEN
from .brief import render_brief

# Keep handles to spawned programs so a long-running server can reap them (no zombies,
# no ResourceWarning) once they finish. The programs themselves are detached.
_children: list[subprocess.Popen] = []


# UNHOARD_HOME / _CONFIG / _CARD_ID are paths and ids; anything that looks like a credential is not.
_SECRET_NAME = re.compile(r"^UNHOARD_.*(TOKEN|SECRET|KEY|PASSWORD|PASSWD|CREDENTIAL)", re.I)


def worker_env(base: Mapping[str, str], card_id: str) -> dict[str, str]:
    """The environment for the launched worker: the user's own, without the X token, the X client secret or any UNHOARD_*
    secret (the worker reads untrusted posts and has tools; it never needs to sync). Enrichment strips the token
    the same way (see ``agents.agent_env``)."""
    env = {k: v for k, v in base.items() if k not in (ENV_TOKEN, ENV_CLIENT_SECRET) and not _SECRET_NAME.match(k)}
    env["UNHOARD_CARD_ID"] = card_id
    return env


class CommandLauncher:
    """``argv`` is a list, never a shell line: the brief contains untrusted text."""

    def __init__(self, config: dict, log_dir: Path | None = None):
        self.argv = list(config.get("argv") or [])
        self.log_dir = log_dir

    def __call__(self, card: dict, note: str = "") -> dict:
        from . import LaunchError
        if not self.argv or not all(isinstance(a, str) for a in self.argv):
            raise LaunchError("no launch command: set [command] argv in the config")
        stdout = subprocess.DEVNULL
        log = None
        if self.log_dir:
            self.log_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            log = open(self.log_dir / f"{card['action_id']}.log", "ab")  # noqa: SIM115 - handed to the child
            stdout = log
        env = worker_env(os.environ, card["action_id"])
        try:
            proc = subprocess.Popen(self.argv, stdin=subprocess.PIPE, stdout=stdout, stderr=subprocess.STDOUT,
                                    env=env, **procs.NEW_SESSION)
        except OSError as exc:
            raise LaunchError(f"could not start {self.argv[0]}: {exc}") from exc
        finally:
            if log:
                log.close()  # the child keeps its own copy of the descriptor
        try:
            assert proc.stdin is not None
            proc.stdin.write(render_brief(card, note).encode("utf-8"))
            proc.stdin.close()
        except BrokenPipeError:
            pass  # the program chose not to read stdin; it is still running or already finished
        _children[:] = [c for c in _children if c.poll() is None] + [proc]
        return {"ref": f"pid:{proc.pid}", "status": "started", "target": "command"}
