"""``unhoard login x``: OAuth 2.0 Authorization Code with PKCE, so nobody has to mint a user token by hand.

You bring your own X developer app (this project cannot ship a client id). The flow:

1. print the app setup steps when no client id is known;
2. start a one-shot server on 127.0.0.1 only, open the browser at X's authorize page, wait for the redirect;
3. check ``state``, exchange the code (with the PKCE verifier) at the token endpoint;
4. ask ``GET /2/users/me`` who logged in, save the numeric id into ``[x] user_id`` when it is empty.

The tokens live in ``<data dir>/x_oauth.json`` (mode 0600 from the moment the file exists, replaced atomically),
never in the SQLite database, the config file or any environment. ``sync`` uses them last: ``X_BEARER_TOKEN`` and
``[x] bearer_token`` still win. An expired access token, or a 401, triggers a refresh; X rotates refresh tokens, so
the new one is saved at once. Endpoints and parameters follow https://docs.x.com/resources/fundamentals/authentication/oauth-2-0/
(authorization code flow, user access token). Only the standard library is used.
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import hmac
import http.client
import http.server
import json
import os
import re
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping

from . import netsafe, sync, tomledit
from .config import ENV_CLIENT_ID, ENV_CLIENT_SECRET, Config, Paths

AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
SCOPES = ("tweet.read", "users.read", "bookmark.read", "offline.access")
DEFAULT_PORT = 8766
CALLBACK_PATH = "/callback"
LOGIN_TIMEOUT = 300  # seconds to wait for the browser
DEFAULT_EXPIRES_IN = 7200  # X access tokens last about two hours
EXPIRY_MARGIN = 60  # refresh a minute early
TOKEN_FILE = "x_oauth.json"
RELOGIN = "run `unhoard login x` again"
_ID = re.compile(r"\d{1,25}")


class LoginError(sync.SyncError):
    """The login (or the use of a saved login) failed. A SyncError, so ``sync`` reports it like any other failure."""


# --- files ------------------------------------------------------------------------------------------------

def token_file(paths: Paths) -> Path:
    return paths.data_dir / TOKEN_FILE


def _write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` through a new 0600 file in the same folder, then ``os.replace``.

    The temporary file is created with mode 0600 (``O_EXCL``), so the tokens are never readable by anyone else,
    not even briefly, whatever the umask is.
    """
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def save_login(paths: Paths, data: Mapping[str, Any]) -> None:
    _write_private(token_file(paths), json.dumps(dict(data), indent=2, sort_keys=True) + "\n")


def load_login(paths: Paths) -> dict[str, Any] | None:
    """The saved login, or None when there is none. Raises LoginError when the file exists but is unusable."""
    path = token_file(paths)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise LoginError(f"cannot read {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise LoginError(f"{path} is not valid JSON; delete it and {RELOGIN}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("access_token"), str) or not data["access_token"]:
        raise LoginError(f"{path} has no access token; delete it and {RELOGIN}")
    return data


@contextlib.contextmanager
def _locked(paths: Paths) -> Iterator[None]:
    """Serialise refreshes between processes (X rotates refresh tokens: two refreshes with one token would lose one)."""
    try:
        import fcntl
    except ImportError:  # not POSIX: no cross-process lock
        yield
        return
    paths.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(paths.data_dir / (TOKEN_FILE + ".lock"), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)  # closing releases the lock


# --- the token endpoint -----------------------------------------------------------------------------------

def _check_url(url: str) -> None:
    """Tokens and the client secret go only to https (or to a loopback test server)."""
    problem = netsafe.credential_url_problem(url)
    if problem:
        raise LoginError(problem)


def _call(url: str, *, fields: Mapping[str, str] | None = None, bearer: str = "", client_id: str = "",
          client_secret: str = "", timeout: int = 30) -> tuple[int, dict]:
    """One request. POST form when ``fields`` is given (Basic auth for a confidential client, else ``client_id`` in
    the body), else GET with a bearer token. Returns (HTTP status, JSON object); never raises on an HTTP status."""
    _check_url(url)
    headers = {"Accept": "application/json"}
    data = None
    if fields is not None:
        body = dict(fields)
        if client_secret:
            headers["Authorization"] = "Basic " + base64.b64encode(f"{client_id}:{client_secret}".encode()).decode()
        else:
            body["client_id"] = client_id
        data = urllib.parse.urlencode(body).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    elif bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data is not None else "GET")
    try:
        with netsafe.strict_opener().open(req, timeout=timeout) as resp:  # https (or loopback in tests); never follows a redirect
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        status, raw = exc.code, exc.read()
        exc.close()
    except (urllib.error.URLError, TimeoutError, OSError, http.client.HTTPException) as exc:
        raise LoginError(f"cannot reach X ({urllib.parse.urlsplit(url).hostname}): {exc}") from exc
    try:
        parsed = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        parsed = {}
    return status, parsed if isinstance(parsed, dict) else {}


def _why(payload: Mapping[str, Any]) -> str:
    """A short, safe reason from an error answer (never a token: error bodies carry none)."""
    parts = [str(payload.get(k)) for k in ("error", "error_description", "detail", "title") if payload.get(k)]
    return re.sub(r"\s+", " ", "; ".join(parts))[:200]


def _endpoint(api_base: str, name: str) -> str:
    return f"{api_base.rstrip('/')}/{name}"


def _tokens_from(payload: Mapping[str, Any], previous: Mapping[str, Any], now: float) -> dict[str, Any]:
    """The saved record from a token answer. A missing refresh_token keeps the old one (rotation is not guaranteed)."""
    access = payload.get("access_token")
    if not isinstance(access, str) or not access:
        raise LoginError("X answered without an access token")
    try:
        lifetime = int(payload.get("expires_in") or DEFAULT_EXPIRES_IN)
    except (TypeError, ValueError):
        lifetime = DEFAULT_EXPIRES_IN
    out = dict(previous)
    out.update(access_token=access, expires_at=int(now) + lifetime,
               refresh_token=str(payload.get("refresh_token") or previous.get("refresh_token") or ""),
               scope=str(payload.get("scope") or previous.get("scope") or ""))
    return out


# --- using a saved login ----------------------------------------------------------------------------------

class StoredLogin:
    """The saved OAuth token of this machine: hands out a valid access token, refreshing when needed."""

    def __init__(self, cfg: Config, now: Callable[[], float] = time.time):
        self.cfg, self.paths, self.now = cfg, cfg.paths, now
        self.api_base = str(cfg.get("x", "api_base"))

    def _expired(self, data: Mapping[str, Any]) -> bool:
        try:
            return float(data.get("expires_at") or 0) - EXPIRY_MARGIN <= self.now()
        except (TypeError, ValueError):
            return True

    def access_token(self) -> str:
        data = load_login(self.paths)
        if data is None:
            raise LoginError(f"no saved X login: {RELOGIN}")
        return self._refresh(stale=data["access_token"]) if self._expired(data) else data["access_token"]

    def refresh_after_401(self, refused: str) -> str:
        """The token ``refused`` was rejected: refresh (unless another process already did) and return the new one."""
        return self._refresh(stale=refused)

    def _refresh(self, stale: str) -> str:
        with _locked(self.paths):
            data = load_login(self.paths)
            if data is None:
                raise LoginError(f"no saved X login: {RELOGIN}")
            if data["access_token"] != stale and not self._expired(data):
                return data["access_token"]  # another process refreshed while we waited for the lock
            refresh_token = str(data.get("refresh_token") or "")
            if not refresh_token:
                raise LoginError(f"the saved X login has expired and cannot be refreshed: {RELOGIN} "
                                 "(enable the offline.access scope)")
            env_id, secret = self.cfg.x_client()
            client_id = str(data.get("client_id") or env_id)
            if not client_id:
                raise LoginError(f"the saved X login has no client id: {RELOGIN}")
            status, payload = _call(_endpoint(self.api_base, "oauth2/token"), client_id=client_id,
                                    client_secret=secret, fields={"grant_type": "refresh_token",
                                                                  "refresh_token": refresh_token})
            if status in (400, 401, 403):
                raise LoginError(f"X rejected the saved login ({_why(payload) or f'HTTP {status}'}): {RELOGIN}"
                                 + (f" (a confidential client also needs {ENV_CLIENT_SECRET})" if not secret else ""))
            if status != 200:
                raise LoginError(f"could not refresh the X login (HTTP {status} {_why(payload)}); try again later")
            fresh = _tokens_from(payload, data, self.now())
            save_login(self.paths, fresh)  # the rotated refresh token is saved before anything else happens
            return fresh["access_token"]


def token_source(cfg: Config) -> str | None:
    """Where the token ``sync`` will use comes from: "env", "config", "login" or None. Never returns the token."""
    origin = cfg.x_token_origin
    if origin:
        return origin
    try:
        return "login" if load_login(cfg.paths) else None
    except LoginError:
        return None


def sync_kwargs(cfg: Config, now: Callable[[], float] = time.time) -> dict[str, Any]:
    """Keyword arguments for ``sync.sync`` / ``sync.refresh``: ``token`` and, for a saved login, a ``fetch`` that
    refreshes the access token on a 401 and retries once. Precedence: X_BEARER_TOKEN, config token, saved login."""
    if token_source(cfg) != "login":
        return {"token": cfg.x_token}
    login = StoredLogin(cfg, now)
    state = {"token": login.access_token()}

    def fetch(url: str, _token: str) -> dict:
        try:
            return sync.fetch_json(url, state["token"])
        except sync.SyncError as exc:
            if exc.status != 401:
                raise
        state["token"] = login.refresh_after_401(state["token"])
        return sync.fetch_json(url, state["token"])

    return {"token": state["token"], "fetch": fetch}


def status_info(cfg: Config, now: Callable[[], float] = time.time) -> dict[str, Any]:
    """Token facts for ``status`` and ``doctor``. Contains no token and no client secret."""
    source = token_source(cfg)
    info: dict[str, Any] = {"x_token_set": source is not None, "x_token_source": source, "x_login": None}
    try:
        data = load_login(cfg.paths)
    except LoginError as exc:
        info["x_login_problem"] = str(exc)
        return info
    if data is not None:
        expires = int(data.get("expires_at") or 0)
        try:
            mode = token_file(cfg.paths).stat().st_mode & 0o777
        except OSError:  # the file vanished (a logout) between the read and here: there is no login
            return info
        info["x_login"] = {
            "user": f"@{data['username']}" if data.get("username") else None, "user_id": data.get("user_id") or None,
            "expires_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(expires)) if expires else None,
            "expired": expires - EXPIRY_MARGIN <= now(), "can_refresh": bool(data.get("refresh_token")),
            "scopes": str(data.get("scope") or "").split(), "file": str(token_file(cfg.paths)),
            "file_mode": format(mode, "03o")}
    return info


def describe_source(info: Mapping[str, Any]) -> tuple[str, str]:
    """``(status, detail)`` for the doctor row, from ``status_info``."""
    if info.get("x_login_problem"):
        return "warn", str(info["x_login_problem"])
    source, login = info.get("x_token_source"), info.get("x_login")
    if source == "env":
        return "pass", "X_BEARER_TOKEN is set (it wins over a config token and a saved login)"
    if source == "config":
        return "pass", "token from [x] bearer_token in the config file (X_BEARER_TOKEN would win; `login x` is safer)"
    if source == "login" and login:
        who = f"{login['user']} " if login["user"] else ""
        if login["expired"]:
            note = "access token expired; it refreshes on the next sync" if login["can_refresh"] else \
                f"access token expired and cannot be refreshed: {RELOGIN}"
            return ("pass" if login["can_refresh"] else "warn"), f"saved login {who}({note})"
        loose = "" if login["file_mode"][1:] == "00" else f"; WARNING: {login['file']} has mode {login['file_mode']}, chmod 600"
        return ("pass" if not loose else "warn"), (
            f"saved login {who}via `login x`; access token valid until {login['expires_at']}"
            f"{' (refreshes automatically)' if login['can_refresh'] else ''}{loose}")
    return "warn", "no X token: run `unhoard login x` (or set X_BEARER_TOKEN)"


# --- the login ---------------------------------------------------------------------------------------------

def pkce_pair() -> tuple[str, str]:
    """``(code_verifier, code_challenge)``: a 86-character URL-safe verifier and its S256 challenge (RFC 7636)."""
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return verifier, challenge


def redirect_uri(port: int) -> str:
    return f"http://127.0.0.1:{port}{CALLBACK_PATH}"


def authorize_url(client_id: str, redirect: str, state: str, challenge: str) -> str:
    query = {"response_type": "code", "client_id": client_id, "redirect_uri": redirect, "scope": " ".join(SCOPES),
             "state": state, "code_challenge": challenge, "code_challenge_method": "S256"}
    return AUTHORIZE_URL + "?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)


SETUP_STEPS = """\
No X client id yet. Create an X developer app of your own (this tool cannot ship one):
  1. Open https://developer.x.com/en/portal/dashboard and create (or pick) a project and an app.
  2. In the app's "User authentication settings" turn on OAuth 2.0. App type: "Native App" is fine
     (no secret needed). "Web App, Automated App or Bot" also works; it gives you a client secret.
  3. Set the Callback URI / Redirect URL to exactly: {redirect}
     (the port can be changed with --port; the URL must match character for character).
     Set any Website URL. App permissions: Read.
  4. Copy the OAuth 2.0 Client ID (and the Client Secret if you chose a confidential app).
Then run again, giving the client id once:
  export {id_env}=...                  # or: unhoard login x --client-id ...  or [x] client_id in the config
  export {secret_env}=...              # only for a confidential app; never put the secret on the command line
  unhoard login x
Scopes requested: {scopes}
Details: docs/x-login.md"""


def setup_steps(port: int) -> str:
    return SETUP_STEPS.format(redirect=redirect_uri(port), id_env=ENV_CLIENT_ID, secret_env=ENV_CLIENT_SECRET,
                              scopes=" ".join(SCOPES))


_PAGE = ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><title>unhoard</title>"
         "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"></head>"
         "<body style=\"font:16px system-ui,sans-serif;max-width:32rem;margin:4rem auto;padding:0 1rem\">"
         "<h1>{title}</h1><p>{text}</p></body></html>")
PAGE_OK = _PAGE.format(title="Done", text="You can close this tab and go back to the terminal.").encode()
PAGE_BAD = _PAGE.format(title="Not completed",
                        text="This login could not be completed. Go back to the terminal for details.").encode()
PAGE_404 = _PAGE.format(title="Not found", text="Nothing here.").encode()


class _Callback(http.server.BaseHTTPRequestHandler):
    """Answers only ``GET /callback``. The page text is fixed: nothing from the request is ever put into HTML."""

    server_version = "unhoard"
    sys_version = ""
    timeout = 5  # a connection that sends nothing must not hold up the one-shot server

    def log_message(self, *args: Any) -> None:
        pass

    def _send(self, code: int, body: bytes) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy", "default-src 'none'; style-src 'unsafe-inline'")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        parts = urllib.parse.urlsplit(self.path)
        if parts.path != CALLBACK_PATH:
            self._send(404, PAGE_404)
            return
        query = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query, keep_blank_values=True).items()}
        outcome = self.server.judge(query)  # type: ignore[attr-defined]
        self._send(200 if outcome == "ok" else 400, PAGE_OK if outcome == "ok" else PAGE_BAD)


class CallbackServer(http.server.HTTPServer):
    """A one-shot server bound to 127.0.0.1 only. ``wait`` returns the authorization code (or raises)."""

    allow_reuse_address = False

    def __init__(self, port: int, state: str):
        self.state = state
        self.result: dict[str, str] | None = None
        super().__init__(("127.0.0.1", port), _Callback)
        self.port = self.server_address[1]

    def judge(self, query: Mapping[str, str]) -> str:
        """"ok" (a good answer, kept), "ignored" (wrong or missing state: another program or page, not our login)."""
        given = query.get("state", "")
        if not given or not hmac.compare_digest(given.encode(), self.state.encode()):
            return "ignored"
        if self.result is None:
            self.result = {k: query.get(k, "") for k in ("code", "error", "error_description")}
            if not self.result["code"] and not self.result["error"]:  # the right state, but nothing to use
                self.result["local_error"] = "the redirect carried no authorization code"
        return "ok" if self.result.get("code") and not self.result.get("error") else "failed"

    def wait(self, timeout: float) -> str:
        deadline = time.monotonic() + timeout
        self.timeout = 0.5
        while self.result is None:
            if time.monotonic() >= deadline:
                raise LoginError(f"timed out after {int(timeout)} s waiting for the browser; nothing was saved")
            self.handle_request()
        if self.result.get("error"):
            code = re.sub(r"[^A-Za-z0-9_.-]", "", self.result["error"])[:40]
            raise LoginError(f"X did not authorize the login ({code}); nothing was saved")
        if not self.result.get("code"):
            raise LoginError(f"{self.result.get('local_error') or 'no authorization code arrived'}; "
                             "nothing was saved. Run the login again")
        return self.result["code"]


class _Closer:
    def __init__(self, server: CallbackServer):
        self.close = server.server_close


class LoginDeclined(LoginError):
    """The user chose to keep the saved login, so no new login was started."""


def _ask_yes_no(question: str) -> bool:
    """y/N on the terminal. Anything but y or yes (including end of input) is no."""
    try:
        return input(question + " [y/N] ").strip().lower() in ("y", "yes")
    except EOFError:
        return False


def _sign_out_previous(cfg: Config, out: Callable[[str], None], *, replace: bool = False,
                       ask: Callable[[str], bool] | None = None) -> None:
    """Revoke and delete a saved login before a new one starts, so revoking can never hit the new tokens (X may keep
    one grant per user and app). The login is gone even if the new authorization is cancelled or fails.

    That is destructive, so it is never silent: ``replace`` says "yes" up front (``--replace``), ``ask`` asks on a
    terminal, and with neither (no terminal, no ``--replace``) the saved login is left alone and the login refuses."""
    if not token_file(cfg.paths).exists():
        return
    if not replace:
        if ask is None:
            raise LoginError("a saved X login exists and there is no terminal to ask. Run `unhoard login x --replace` "
                             "to sign it out (revoke it at X, delete it) and log in again, or `unhoard logout x` "
                             "first. Nothing was changed.")
        if not ask("A saved X login exists. Sign it out (revoke it at X and delete it) and log in again?"):
            raise LoginDeclined("kept the saved login; nothing was changed")
    out("Signing the saved X login out first (revoking it at X and deleting it), then starting a new one:")
    run_logout(cfg, out)


def run_login(cfg: Config, *, client_id: str, client_secret: str = "", port: int = DEFAULT_PORT,
              timeout: float = LOGIN_TIMEOUT, open_browser: Callable[[str], Any] | None = None,
              out: Callable[[str], None] = print, now: Callable[[], float] = time.time, replace: bool = False,
              ask: Callable[[str], bool] | None = None) -> dict[str, Any]:
    """The whole login. Returns what was saved (without any token). A saved login is only signed out with
    ``replace=True`` or when ``ask`` (a y/N prompt) says yes; otherwise this raises ``LoginError``."""
    api_base = str(cfg.get("x", "api_base"))
    _check_url(api_base)
    state = secrets.token_urlsafe(32)
    verifier, challenge = pkce_pair()
    try:
        server = CallbackServer(port, state)
    except OSError as exc:
        raise LoginError(f"cannot listen on 127.0.0.1:{port} ({exc.strerror or exc}); is another login running? "
                         "Pick another port with --port and use the same port in the app's callback URL") from exc
    redirect = redirect_uri(server.port)
    url = authorize_url(client_id, redirect, state, challenge)
    with contextlib.closing(_Closer(server)):
        # before the new authorization: X may revoke per user and app, not per token
        _sign_out_previous(cfg, out, replace=replace, ask=ask)
        out("Open this address in a browser and approve the access (it opens by itself when it can):\n\n"
            f"  {url}\n\n"
            f"Waiting up to {int(timeout)} s for the redirect to {redirect}.\n"
            f"On a remote machine, forward the port first, then open the address on your own computer:\n"
            f"  ssh -L {server.port}:127.0.0.1:{server.port} <this-machine>")
        with contextlib.suppress(Exception):
            (open_browser or webbrowser.open)(url)
        code = server.wait(timeout)
    status, payload = _call(_endpoint(api_base, "oauth2/token"), client_id=client_id, client_secret=client_secret,
                            fields={"grant_type": "authorization_code", "code": code, "redirect_uri": redirect,
                                    "code_verifier": verifier})
    if status != 200:
        raise LoginError(f"X refused the code exchange ({_why(payload) or f'HTTP {status}'}). Check the client id, "
                         f"that the callback URL in the app is exactly {redirect}, and that OAuth 2.0 is on.")
    record = _tokens_from(payload, {"client_id": client_id, "obtained_at": int(now())}, now())
    with _locked(cfg.paths):  # a refresh or a logout in another process must not interleave with the save
        save_login(cfg.paths, record)
    granted = set(record["scope"].split())
    if granted and "bookmark.read" not in granted:
        out("WARNING: X did not grant bookmark.read; enable it for the app and log in again.")
    if not record["refresh_token"]:
        out("WARNING: no refresh token was issued (offline.access); the login lasts about two hours.")
    status, payload = _call(_endpoint(api_base, "users/me"), bearer=record["access_token"])
    user = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    user_id, username = str(user.get("id") or ""), str(user.get("username") or "")
    if status != 200 or not _ID.fullmatch(user_id):
        out(f"Tokens saved, but GET /2/users/me failed ({_why(payload) or f'HTTP {status}'}). "
            "Set [x] user_id in the config by hand, or sync will look it up.")
        return {"user_id": "", "username": "", "file": str(token_file(cfg.paths))}
    with _locked(cfg.paths):  # add the user to whatever is on disk now (a refresh may have rotated the tokens)
        current = load_login(cfg.paths) or record
        current.update(user_id=user_id, username=username)
        save_login(cfg.paths, current)
    out(f"Logged in as @{username} (id {user_id})")
    _remember_user_id(cfg, user_id, out)
    return {"user_id": user_id, "username": username, "file": str(token_file(cfg.paths))}


def _remember_user_id(cfg: Config, user_id: str, out: Callable[[str], None]) -> None:
    current = str(cfg.get("x", "user_id", "") or "")
    if current and current != user_id:
        out(f"WARNING: [x] user_id is {current} but this login is {user_id}. Bookmarks are read for the id in the "
            "config; fix it if you meant the logged-in account.")
        return
    if current or not cfg.paths.config_file.exists():
        return
    try:
        tomledit.update_file_strings(cfg.paths.config_file, "x", {"user_id": user_id})
        out(f"Saved [x] user_id = {user_id!r} in {cfg.paths.config_file}")
    except (OSError, tomledit.TomlEditError) as exc:
        out(f"Could not update {cfg.paths.config_file} ({exc}); set [x] user_id = \"{user_id}\" by hand.")


def _revoke(cfg: Config, data: Mapping[str, Any]) -> bool:
    """Best-effort revoke of a saved login's tokens at X. True when every call answered 200."""
    env_id, secret = cfg.x_client()
    client_id = str(data.get("client_id") or env_id)
    ok = True
    for kind in ("refresh_token", "access_token"):
        value = str(data.get(kind) or "")
        if not value or not client_id:
            continue
        try:
            status, _ = _call(_endpoint(str(cfg.get("x", "api_base")), "oauth2/revoke"), client_id=client_id,
                              client_secret=secret, fields={"token": value})
            ok = ok and status == 200
        except LoginError:
            ok = False
    return ok


def run_logout(cfg: Config, out: Callable[[str], None] = print) -> int:
    """Revoke the saved tokens at X (best effort, https://docs.x.com .../oauth2/revoke) and delete the file.

    The whole thing runs under the same lock as a refresh, so a refresh in another process cannot rotate the
    tokens between the read and the revoke, or write the file back after it is deleted.
    """
    path = token_file(cfg.paths)
    with _locked(cfg.paths):
        try:
            data = load_login(cfg.paths)
        except LoginError:
            data = None
        if data is None and not path.exists():
            out("No saved X login.")
            return 0
        if data is not None and not _revoke(cfg, data):
            out("Could not revoke the token at X (offline, or a confidential app without "
                f"{ENV_CLIENT_SECRET}). Remove the app under X Settings > Security > Connected apps if you want to.")
        try:
            path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            out(f"Could not delete {path}: {exc}")
            return 1
    out(f"Deleted {path}. Logged out of X.")
    return 0


def _has_terminal() -> bool:
    try:
        return sys.stdin.isatty() and sys.stdout.isatty()
    except (AttributeError, ValueError):
        return False


def login_command(cfg: Config, *, client_id: str = "", port: int = DEFAULT_PORT, timeout: float = LOGIN_TIMEOUT,
                  no_browser: bool = False, replace: bool = False) -> int:
    """The ``login x`` command: setup steps when the client id is missing, else the flow. Exit status 0/1.

    A saved login is replaced only on ``--replace`` or after a "y" at the prompt (which needs a terminal)."""
    env_id, secret = cfg.x_client()
    client_id = client_id or env_id
    if not client_id:
        print(setup_steps(port))
        return 1
    try:
        run_login(cfg, client_id=client_id, client_secret=secret, port=port, timeout=timeout,
                  open_browser=(lambda _url: None) if no_browser else None, replace=replace,
                  ask=_ask_yes_no if _has_terminal() else None)
    except LoginDeclined as exc:
        print(f"Cancelled: {exc}.", file=sys.stderr)
        return 1
    except LoginError as exc:
        print(f"login failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nCancelled; nothing was saved.", file=sys.stderr)
        return 130
    return 0
