"""``unhoard try``: see the product in one command, without touching your own setup.

Everything lives in a temporary folder that is removed on exit: a config, a database with the fictional demo cards
and nothing else. No X token and no agent are needed, and your real config and data are never read or written.
The print adapter is set explicitly, so the first button ("Show brief") prints the brief in this terminal and starts
nothing, even when an Omnigent server runs on this machine.
"""
from __future__ import annotations

import shutil
import signal
import sys
import tempfile
from pathlib import Path

from .config import Config, Paths


def make_tryout_config(root: Path) -> Config:
    """A config and data directory inside ``root`` only; the environment (X token, HOME overrides) is ignored."""
    paths = Paths((root / "data").resolve(), (root / "config" / "config.toml").resolve())
    return Config({"launch": {"adapter": "print"}}, paths, env={})


STOP_SIGNALS = ("SIGTERM", "SIGHUP")


def _terminate(_signum, _frame):
    raise KeyboardInterrupt  # the finally block below removes the temporary folder


def run_tryout(port: int | None = None, open_browser: bool = True) -> int:
    from .demo import seed
    from .server import make_server, serve
    from .store import connect
    root = Path(tempfile.mkdtemp(prefix="unhoard-try-"))
    old_handlers: dict[int, object] = {}
    try:
        for name in STOP_SIGNALS:  # a closed terminal (SIGHUP) must clean up like Ctrl+C and kill do
            number = getattr(signal, name, None)
            if number is None:  # no such signal on this platform
                continue
            try:
                old_handlers[number] = signal.signal(number, _terminate)
            except (ValueError, OSError):  # not the main thread
                pass
        cfg = make_tryout_config(root)
        db = connect(cfg.paths)
        try:
            seed(db)
        finally:
            db.close()
        server = make_server(cfg, 0 if port is None else port)  # port 0: any free loopback port
        server.quiet = True  # no request lines between the URL and the brief that Go prints
        print("Trying unhoard with four fictional sample cards.\n"
              "Nothing here is real, nothing is saved, and your own config and data are not touched.\n"
              "Show brief prints the task brief in this terminal instead of starting anything.")
        serve(cfg, open_browser=open_browser, server=server, stop_hint="Ctrl+C to stop")
        return 0
    except OSError as exc:
        print(f"error: cannot start the server: {exc} (try --port)", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(root, ignore_errors=True)
        for number, old_handler in old_handlers.items():
            try:
                signal.signal(number, old_handler)
            except (ValueError, OSError):
                pass
        print("Removed the temporary sample data.")
