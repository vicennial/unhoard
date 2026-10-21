"""Shared test helpers. Importing this module first also makes ``src`` importable without an install."""
import contextlib
import json
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT / "src") not in sys.path:
    sys.path.insert(0, str(ROOT / "src"))

from unhoard import launch  # noqa: E402
from unhoard.config import Config, Paths  # noqa: E402
from unhoard.store import connect  # noqa: E402

# The default launch adapter ("auto") asks a loopback address whether Omnigent runs. A test must never reach a real
# server on the developer's machine, so the probe answers "no" unless a test patches it (launch.probe_omnigent).
REAL_PROBE = launch.probe_omnigent  # for the tests that point it at a fake server
launch.probe_omnigent = lambda api: False

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class TempCase(unittest.TestCase):
    """A fresh data dir, config path and database per test."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name).resolve()
        self.paths = Paths(self.tmp / "data", self.tmp / "config" / "config.toml")
        self.db = connect(self.paths)
        self.addCleanup(self.db.close)

    def config(self, **sections):
        return Config(sections, self.paths, env={})


def seed_basic(db):
    """Three tweets and three proposals mirroring the ranking cases: low / high / medium priority."""
    db.executescript("""
    INSERT INTO bookmarks(tweet_id,text,author_username,author_name,first_seen_at,last_seen_at,source_url,raw_json,state)
      VALUES('1','post one','a','A','2026-10-01','2026-10-01','https://x.com/a.example/status/1','{}','enriched'),
            ('2','post two','b','B','2026-10-02','2026-10-02','https://x.com/b.example/status/2','{}','enriched');
    INSERT INTO enrichments(tweet_id,summary,why_saved,confidence,enriched_at) VALUES('1','sum one','why one',0.8,'2026-10-01'),
                                  ('2','sum two','why two',0.9,'2026-10-02');
    INSERT INTO actions(action_id,tweet_id,title,brief,label,priority,created_at) VALUES
      ('BMK-0000000A','1','Low task','d','personal','low','2026-10-10T00:00:00Z'),
      ('BMK-0000000B','2','High task','d','work','high','2026-09-21T00:00:00Z'),
      ('BMK-0000000C','1','Medium task','d','personal','medium','2026-10-12T00:00:00Z');
    """)
    db.commit()


def real_enriched(db, tid="1800000000000000001"):
    """One real (not demo-id) bookmark with a task proposal."""
    from unhoard.enrich import store_result
    from unhoard.store import timestamp
    stamp = timestamp()
    db.execute("""INSERT INTO bookmarks(tweet_id,text,author_id,author_username,author_name,tweet_created_at,first_seen_at,
                  last_seen_at,source_url,raw_json) VALUES(?,?,?,?,?,?,?,?,?,'{}')""",
               (tid, "a post", "1", "someone", "Someone", stamp, stamp, stamp, "https://x.com/someone.example/status/" + tid))
    store_result(db, tid, {"summary": "s", "why_saved": "w", "confidence": 0.8,
                           "tasks": [{"title": "T", "brief": "B", "label": "work", "priority": "high"}]})
    db.commit()


class Recorder(BaseHTTPRequestHandler):
    """Records every request on the server and answers with ``server.responder(handler, body)``."""

    def log_message(self, *args):
        pass

    def _handle(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else b""
        self.server.requests.append({"method": self.command, "path": self.path, "headers": dict(self.headers),
                                     "body": body})
        code, payload, *extra = self.server.responder(self, body)  # an optional third item: extra response headers
        raw = json.dumps(payload).encode()
        self.send_response(code)
        for name, value in (extra[0] if extra else {}).items():
            self.send_header(name, value)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    do_GET = do_POST = do_DELETE = _handle


@contextlib.contextmanager
def fake_http(responder):
    """A loopback HTTP server; yields (base_url, recorded_requests)."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), Recorder)
    server.requests = []
    server.responder = responder
    thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", server.requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


import contextlib  # noqa: E402
import os  # noqa: E402
import socket  # noqa: E402
from unittest import mock  # noqa: E402


@contextlib.contextmanager
def fake_home(home, claude_servers=None):
    """Point HOME at ``home`` (and forget CLAUDE_CONFIG_DIR / CODEX_HOME); optionally write ~/.claude.json."""
    home = Path(home)
    home.mkdir(parents=True, exist_ok=True)
    if claude_servers is not None:
        (home / ".claude.json").write_text(json.dumps({"numStartups": 3, "mcpServers": claude_servers}))
    with mock.patch.dict(os.environ, {"HOME": str(home)}):
        for name in ("CLAUDE_CONFIG_DIR", "CODEX_HOME", "X_BEARER_TOKEN"):
            os.environ.pop(name, None)
        yield home


# a user's Claude MCP servers: two known memory servers, an unknown one and a web one; the secrets must never leak
SECRET = "sk-SECRET-TOKEN-DO-NOT-PRINT"
CLAUDE_SERVERS = {
    "gbrain": {"type": "http", "url": "https://brain.example.invalid/gbrain/mcp", "headers": {"Authorization": "Bearer " + SECRET}},
    "notes-graph": {"type": "stdio", "command": "uvx", "args": ["basic-memory", "mcp"], "env": {"TOKEN": SECRET}},
    "exa": {"type": "http", "url": "https://mcp.exa.example.invalid/mcp", "headers": {"x-api-key": SECRET}},
    "memory": {"type": "stdio", "command": "npx", "args": ["-y", "@modelcontextprotocol/server-memory"]},
}


@contextlib.contextmanager
def garbage_listener(banner=b"SSH-2.0-OpenSSH_9.6\r\n"):
    """A loopback listener that is not HTTP (it answers like an SSH server): http.client raises BadStatusLine."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    srv.settimeout(0.1)
    stop = threading.Event()

    def run():
        while not stop.is_set():
            try:
                conn, _ = srv.accept()
            except OSError:
                continue
            with contextlib.suppress(OSError):
                conn.recv(4096)
                conn.sendall(banner)
            conn.close()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    finally:
        stop.set()
        thread.join(timeout=5)
        srv.close()
