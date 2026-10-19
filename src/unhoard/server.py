"""Local card page: ``unhoard serve``.

Binds to 127.0.0.1 only and never takes a host option. Because any web page you
visit can make your browser send requests to localhost, state-changing calls
are guarded three ways: a custom header (which a foreign page cannot add
without a CORS preflight that this server never grants), a foreign-Origin
check, and a Host check against DNS rebinding.
"""
from __future__ import annotations

import functools
import html
import json
import os
import re
import sqlite3
import sys
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Callable
from urllib.parse import parse_qs, urlparse

from . import firstrun, postdata
from . import queue as cards
from .config import Config
from .launch import AutoLauncher, TargetChanged, make_launcher, resolve_target
from .store import connect

CSRF_HEADER = "X-Unhoard"
BIND = "127.0.0.1"
MAX_BODY = 16384
DECIDE_PATH = re.compile(r"^/api/cards/(BMK-[0-9A-F]{8})/decide$")
STATIC = {"/": ("index.html", "text/html; charset=utf-8"),
          "/static/app.css": ("app.css", "text/css; charset=utf-8"),
          "/static/app.js": ("app.js", "application/javascript; charset=utf-8")}
# img-src https: covers profile pictures and photos; media-src is limited to the host that serves X videos
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src https: data:; "
       "media-src https://video.twimg.com; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'; frame-ancestors 'none'")
_URL_CHARS = r"""[^\s<>"'\]]"""
# What counts as a link in model-written text: anything with a scheme (https://, ftp://, file:///, ...), a few
# schemes without //, www. hosts, scp-style git@host:path, host/path, and bare domains with a common TLD.
# Every repetition before the URL body is bounded (a scheme is at most 32 characters, a host label 63, a host 8 labels),
# so text such as "a.a.a.a..." or "a+a+a+...@" costs a constant amount per start position, not a rescan of the rest
# of the text. The body after the prefix is consumed once, so the whole match is linear.
_HOST = r"(?:[a-z0-9-]{1,63}\.){1,8}"
URL_IN_TEXT = re.compile(
    r"\b[a-z][a-z0-9+.\-]{0,31}://" + _URL_CHARS + "+"
    r"|\b(?:mailto|javascript|data|vbscript|tel|sms):" + _URL_CHARS + "+"
    r"|\bwww\." + _URL_CHARS + "+"
    r"|\b[\w.+-]{1,64}@[a-z0-9.-]{1,255}:" + _URL_CHARS + "+"
    r"|\b" + _HOST + r"[a-z]{2,24}/" + _URL_CHARS + "*"
    r"|\b" + _HOST + r"(?:com|org|net|io|dev|ai|co|app|edu|gov|info|me|xyz|gg|ly|uk|de|fr|eu|us|ca|au|tv|fm|cc|"
    r"blog|tech|cloud|site|page|news|world)\b", re.I)
# Host: name[:port] and Origin: https://name[:port], matched against [server] allowed_hosts (IPv6 and userinfo never
# match). An allowed name is only ever reached through an HTTPS proxy such as `tailscale serve`, so its Origin must be
# https, and neither header may carry the server's own port (see Handler._host_ok).
HOST_HEADER = re.compile(r"([A-Za-z0-9.-]{1,253})(?::([0-9]{1,5}))?")
ORIGIN_HEADER = re.compile(r"https://([A-Za-z0-9.-]{1,253})(?::([0-9]{1,5}))?")
IDENTITY_HEADER = "Tailscale-User-Login"  # added by `tailscale serve` for tailnet users; Funnel requests carry none
MAX_RELATE_URLS = 10
LAUNCHED_LIMIT = 20

Opener = Callable[[], sqlite3.Connection]


class BookmarksServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port: int, open_db: Opener, launcher, daily_cards: int, later_days: int,
                 show_media: bool = True, cfg: Config | None = None):
        super().__init__((BIND, port), Handler)
        self.open_db = open_db
        self.launcher = launcher
        self.daily_cards = daily_cards
        self.later_days = later_days
        self.show_media = show_media
        self.cfg = cfg  # read-only, for the first-run checklist; None serves no /api/setup
        self.quiet = False
        self.allowed_hosts = {f"127.0.0.1:{self.server_port}", f"localhost:{self.server_port}"}
        self.allowed_origins = {f"http://{h}" for h in self.allowed_hosts}
        # extra names from [server] allowed_hosts (a private-network name for phone access): matched by name, with or
        # without a port. The server still binds 127.0.0.1 only; a reverse proxy such as `tailscale serve` reaches it.
        self.extra_hosts = frozenset(cfg.allowed_hosts()) if cfg is not None else frozenset()
        self.require_identity = bool(cfg.get("server", "require_tailscale_identity", True)) if cfg is not None else True

    def launch_info(self) -> dict:
        """What Go will do now, for the page's button: the ``configured`` adapter, the ``target`` (print, command or
        omnigent) and the button text ``go_label``. Resolved at each call, so it follows Omnigent starting or stopping."""
        if self.cfg is None:
            return {"configured": None, "target": None, "go_label": "Go"}
        target = resolve_target(self.cfg)
        return {"configured": target.configured, "target": target.effective, "go_label": target.go_label}


def make_server(cfg: Config, port: int | None = None, launcher=None) -> BookmarksServer:
    return BookmarksServer(
        int(cfg.get("server", "port", 8765) if port is None else port),
        lambda: connect(cfg.paths),
        launcher or make_launcher(cfg),
        int(cfg.get("queue", "daily_cards", cards.DAILY_CARDS)),
        int(cfg.get("queue", "later_days", cards.LATER_DAYS)),
        bool(cfg.get("server", "show_media", True)),
        cfg,
    )


def _asset(name: str) -> bytes:
    return (resources.files("unhoard") / "web" / name).read_bytes()


def _trim(url: str) -> str:
    """Drop sentence punctuation after a URL, and a closing parenthesis only when it has no opening one."""
    while url:
        if url[-1] in ".,;:!?" or (url[-1] == ")" and url.count(")") > url.count("(")):
            url = url[:-1]
        else:
            break
    return url


def _all_relate_urls(card: dict) -> list[str]:
    found: list[str] = []
    for key in ("title", "brief", "why_saved"):
        for match in URL_IN_TEXT.findall(str(card.get(key) or "")):
            url = _trim(match)
            if url and url not in found:
                found.append(url)
    return found


def relate_urls(card: dict) -> list[str]:
    """URLs in the text the relate agent wrote (title, brief, why_saved), at most ``MAX_RELATE_URLS``. That text
    came from a model that saw untrusted content, so the page shows them for the user to check before pressing
    Go. Each is returned as written (the scheme included); ``relate_urls_more`` counts the ones left out."""
    return _all_relate_urls(card)[:MAX_RELATE_URLS]


def _media_item(item: dict) -> dict:
    """One attachment for the page: https (or data: image) preview, https video, sizes and alt text."""
    url = item.get("url")
    ok = isinstance(url, str) and (url.startswith("https://") or url.startswith("data:image/"))
    video = item.get("video_url")
    return {"type": item.get("media_type") or "photo", "url": url if ok else None,
            "video_url": video if isinstance(video, str) and video.startswith("https://") else None,
            "width": item.get("width"), "height": item.get("height"), "duration_ms": item.get("duration_ms"),
            "alt_text": item.get("alt_text")}


def public_card(card: dict, show_media: bool, media: list[dict] | None = None) -> dict:
    """Fields the page may see. Local file paths and raw JSON stay server-side."""
    keys = ("action_id", "title", "brief", "label", "priority", "author_username", "author_name", "text",
            "source_url", "tweet_created_at", "first_seen_at", "summary", "why_saved", "confidence", "launch_error")
    out = {k: card.get(k) for k in keys}
    items = [_media_item(m) for m in media or []] if show_media else []
    out["media_url"] = card.get("media_url") if show_media else None
    out["media"] = [m for m in items if m["url"]]
    avatar = card.get("author_avatar_url")
    out["author_avatar_url"] = avatar if show_media and isinstance(avatar, str) and (
        avatar.startswith("https://") or avatar.startswith("data:image/")) else None
    out["author_verified"] = bool(card.get("author_verified"))
    out["metrics"] = postdata.load_metrics(card.get("metrics_json"))
    out["text_display"] = card.get("text_display") or html.unescape(card.get("text") or "")
    out["links"] = postdata.load_links(card.get("links_json"))
    urls = _all_relate_urls(card)  # once per card
    out["relate_urls"] = urls[:MAX_RELATE_URLS]
    out["relate_urls_more"] = max(0, len(urls) - MAX_RELATE_URLS)
    return out


class Handler(BaseHTTPRequestHandler):
    server: BookmarksServer
    server_version = "unhoard"

    def log_message(self, fmt: str, *args) -> None:  # never log request bodies (notes can be private)
        if self.server.quiet:
            return
        sys.stderr.write("unhoard: " + (fmt % args) + "\n")

    # -- plumbing ---------------------------------------------------------
    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, payload: dict) -> None:
        self._send(code, json.dumps(payload, ensure_ascii=False).encode(), "application/json; charset=utf-8")

    def _port_is_ours(self, port: str | None) -> bool:
        """A name that arrives with this server's own port is a browser talking to it directly (a DNS-rebinding page
        is served from http://NAME:PORT). A proxy on 443 sends no port, or its own HTTPS port, never ours."""
        own = self.server.server_port
        return int(port) == own if port else own == 80  # a bare name means port 80 over plain http

    def _host_ok(self) -> bool:
        """Host is loopback, or an allowed name arriving through a proxy. For an allowed name the request must also
        carry the proxy's identity header (unless ``require_tailscale_identity`` is off); the header's value is never
        read, logged or echoed. ``self.via_extra`` records which case it was, for the Origin check."""
        self.via_extra = False
        host = self.headers.get("Host", "")
        if host in self.server.allowed_hosts:
            return True
        match = HOST_HEADER.fullmatch(host)
        if not match or match.group(1).lower() not in self.server.extra_hosts or self._port_is_ours(match.group(2)):
            return False
        if self.server.require_identity and not self.headers.get(IDENTITY_HEADER):
            return False
        self.via_extra = True
        return True

    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if origin is None or origin in self.server.allowed_origins:
            return True
        match = ORIGIN_HEADER.fullmatch(origin)
        return bool(match) and self.via_extra and match.group(1).lower() in self.server.extra_hosts \
            and not self._port_is_ours(match.group(2))

    # -- routes -----------------------------------------------------------
    def do_GET(self) -> None:
        if not self._host_ok() or not self._origin_ok():
            return self._json(403, {"ok": False, "message": "forbidden"})
        url = urlparse(self.path)
        if url.path in STATIC:
            name, ctype = STATIC[url.path]
            return self._send(200, _asset(name), ctype)
        if url.path == "/api/cards":
            query = parse_qs(url.query)
            db = self.server.open_db()
            try:
                board = cards.board(db, daily_cards=self.server.daily_cards,
                                    more=query.get("more", ["0"])[0] == "1")
                sent = cards.launched(db, LAUNCHED_LIMIT)
                media = cards.media_for(db, [c["tweet_id"] for c in board["picks"] + board["waiting"] + sent])
            finally:
                db.close()
            show = self.server.show_media
            board["picks"] = [public_card(c, show, media.get(c["tweet_id"])) for c in board["picks"]]
            board["waiting"] = [public_card(c, show, media.get(c["tweet_id"])) for c in board["waiting"]]
            board["launched"] = [{**public_card(c, show, media.get(c["tweet_id"])), "launch_ref": c.get("launch_ref"),
                                  "launch_status": c.get("launch_status"), "decided_at": c.get("decided_at")}
                                 for c in sent]
            return self._json(200, {"ok": True, **board, "launch": self.server.launch_info()})
        if url.path == "/api/setup" and self.server.cfg is not None:
            db = self.server.open_db()
            try:
                return self._json(200, firstrun.setup_status(self.server.cfg, db))
            finally:
                db.close()
        self._json(404, {"ok": False, "message": "not found"})

    def _target_changed(self, exc: TargetChanged) -> None:
        """409: the button the page showed is no longer what Go does. Nothing was started and the card is unchanged."""
        self._json(409, {"ok": False, "reason": "target_changed", "message": str(exc),
                         "launch": self.server.launch_info()})

    def do_POST(self) -> None:
        if not self._host_ok() or not self._origin_ok() or self.headers.get(CSRF_HEADER) != "1":
            return self._json(403, {"ok": False, "message": "forbidden"})
        match = DECIDE_PATH.match(urlparse(self.path).path)
        if not match:
            return self._json(404, {"ok": False, "message": "not found"})
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            length = -1
        if not 0 < length <= MAX_BODY or self.headers.get_content_type() != "application/json":
            return self._json(400, {"ok": False, "message": "expected a small JSON body"})
        try:
            body = json.loads(self.rfile.read(length))
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            return self._json(400, {"ok": False, "message": "invalid JSON"})
        expect = body.get("expect_target")  # the target the page showed on the first button (Go only)
        if expect is not None and expect not in ("print", "command", "omnigent"):
            return self._json(400, {"ok": False, "message": "expect_target must be print, command or omnigent"})
        launcher = self.server.launcher
        if body.get("decision") == "go" and expect:
            if self.server.cfg is not None:  # refuse before the card is touched
                now = resolve_target(self.server.cfg).effective
                if now != expect:
                    return self._target_changed(TargetChanged(expect, now))
            if isinstance(launcher, AutoLauncher):  # and again at the moment of launching, which closes the race
                launcher = functools.partial(launcher, expect=expect)
        db = self.server.open_db()
        try:
            result = cards.decide(db, match.group(1), body.get("decision", ""), body.get("note") or "",
                                  launch=launcher, later_days=self.server.later_days)
        except TargetChanged as exc:
            return self._target_changed(exc)
        except ValueError as exc:
            return self._json(400, {"ok": False, "message": str(exc)})
        except cards.NotOpenError as exc:
            return self._json(409, {"ok": False, "message": str(exc)})
        except LookupError as exc:
            return self._json(404, {"ok": False, "message": str(exc)})
        except Exception as exc:  # launch failure: the card was rolled back to "proposed"
            return self._json(502, {"ok": False, "message": f"launch failed, the card is back in the queue: {str(exc)[:300]}"})
        finally:
            db.close()
        self._json(200, result)


def can_open_browser(env=None, platform: str | None = None) -> bool:
    """False in a headless session, where opening a browser would fail or start a text browser in this terminal.

    macOS and Windows always have a desktop. Elsewhere a graphical session sets DISPLAY or WAYLAND_DISPLAY.
    """
    env = os.environ if env is None else env
    platform = sys.platform if platform is None else platform
    if platform in ("darwin", "win32", "cygwin"):
        return True
    return bool(env.get("DISPLAY") or env.get("WAYLAND_DISPLAY"))


def open_in_browser(url: str) -> bool:
    """Open ``url`` in the default browser. Never raises; False when there is no display or the open failed."""
    if not can_open_browser():
        return False
    try:
        return bool(webbrowser.open(url))
    except Exception:  # webbrowser.Error, or a launcher that fails in an odd way: the URL is printed anyway
        return False


def serve(cfg: Config, port: int | None = None, open_browser: bool = False, server: BookmarksServer | None = None,
          stop_hint: str = "Ctrl-C to stop") -> None:
    """Serve the card page until Ctrl-C. Prints the URL; with ``open_browser`` also opens it once listening."""
    server = server or make_server(cfg, port)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"unhoard card page: {url}  ({stop_hint})", flush=True)
    try:
        if open_browser and not open_in_browser(url):
            print("Could not open a browser here. Open the address above in your browser.", flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
