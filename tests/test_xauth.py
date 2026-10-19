"""``login x`` (OAuth 2.0 + PKCE): no network, X is a local HTTP server and the browser is a function."""
import helpers
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import stat
import sys
import threading
import tomllib
import unittest
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from unittest import mock

from unhoard import agents, cli, doctor, omnigent_detect, store, sync, tomledit, xauth
from unhoard.config import Config
from unhoard.launch.command import worker_env

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SCOPE = "tweet.read users.read bookmark.read offline.access"
SECRET = "client-secret-DO-NOT-PRINT"


class FakeX:
    """Just enough of X: authorize happens in the "browser"; token, revoke, users/me and bookmarks are real HTTP."""

    def __init__(self):
        self.challenge = ""
        self.access = {}  # valid access tokens
        self.refresh = {}  # valid refresh tokens
        self.n = 0
        self.token_calls = []
        self.revoked = []
        self.fail_refresh = None  # (status, payload)
        self.fail_revoke = False

    def mint(self):
        self.n += 1
        self.access[f"AT{self.n}"] = True
        self.refresh[f"RT{self.n}"] = True
        return {"access_token": f"AT{self.n}", "refresh_token": f"RT{self.n}", "expires_in": 7200,
                "scope": SCOPE, "token_type": "bearer"}

    def __call__(self, handler, body):
        path = handler.path.split("?")[0]
        bearer = (handler.headers.get("Authorization") or "").removeprefix("Bearer ")
        if path == "/2/oauth2/token":
            form = {k: v[0] for k, v in urllib.parse.parse_qs(body.decode()).items()}
            self.token_calls.append({"form": form, "auth": handler.headers.get("Authorization")})
            if form.get("grant_type") == "authorization_code":
                digest = hashlib.sha256(form["code_verifier"].encode()).digest()
                ok = base64.urlsafe_b64encode(digest).rstrip(b"=").decode() == self.challenge
                if form.get("code") != "good-code" or not ok:
                    return 400, {"error": "invalid_request", "error_description": "bad code or verifier"}
                return 200, self.mint()
            if self.fail_refresh:
                return self.fail_refresh
            if self.refresh.pop(form.get("refresh_token"), None):
                return 200, self.mint()
            return 400, {"error": "invalid_request", "error_description": "Value passed for the token was invalid."}
        if path == "/2/oauth2/revoke":
            self.revoked.append(urllib.parse.parse_qs(body.decode()).get("token", [""])[0])
            return (500, {}) if self.fail_revoke else (200, {"revoked": True})
        if path == "/2/users/me":
            return (200, {"data": {"id": "4242", "username": "alice", "name": "Alice"}}) if bearer in self.access \
                else (401, {"title": "Unauthorized"})
        if path.endswith("/bookmarks"):
            if bearer not in self.access:
                return 401, {"title": "Unauthorized"}
            return 200, {"data": [{"id": "77", "text": "hello", "author_id": "9"}],
                         "includes": {"users": [{"id": "9", "username": "bob", "name": "Bob"}]}, "meta": {}}
        return 404, {}


def browser(x, *answers):
    """An ``open_browser`` that, like a person clicking Approve, calls the redirect URL for each answer in turn.
    An answer is a dict of query parameters, or a function of the authorize query. Records every page it got."""
    pages = []

    def open_url(url):
        query = {k: v[0] for k, v in urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).items()}
        x.challenge = query["code_challenge"]
        open_url.query = query

        def go():
            for answer in answers:
                params = answer(query) if callable(answer) else answer
                target = query["redirect_uri"] + "?" + urllib.parse.urlencode(params)
                try:
                    with NO_PROXY.open(target, timeout=10) as resp:
                        pages.append((resp.status, resp.read().decode()))
                except urllib.error.HTTPError as exc:
                    pages.append((exc.code, exc.read().decode()))
                    exc.close()
        thread = threading.Thread(target=go, daemon=True)
        thread.start()
        open_url.thread = thread

    open_url.pages = pages
    return open_url


def good(query):
    return {"code": "good-code", "state": query["state"]}


class XCase(helpers.TempCase):
    def setUp(self):
        super().setUp()
        self.x = FakeX()
        ctx = helpers.fake_http(self.x)
        self.base, self.requests = ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.api = self.base + "/2"
        self.paths.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.config_file.write_text('# my config\n[x]\n# the id\nuser_id = ""\nmax_items = 500\n')

    def cfg(self, env=None, **x):
        data = tomllib.loads(self.paths.config_file.read_text())
        data.setdefault("x", {}).update({"api_base": self.api, **x})
        return Config(data, self.paths, env=env or {})

    def login(self, *answers, cfg=None, secret="", timeout=10, **kw):
        lines = []
        b = browser(self.x, *(answers or (good,)))
        info = xauth.run_login(cfg or self.cfg(), client_id="CID", client_secret=secret, port=0, timeout=timeout,
                               open_browser=b, out=lines.append, **kw)
        b.thread.join(10)
        return info, "\n".join(lines), b

    def stored(self):
        return json.loads(xauth.token_file(self.paths).read_text())

    def write_login(self, **over):
        record = {"access_token": "OLD-AT", "refresh_token": "OLD-RT", "expires_at": 1, "scope": SCOPE,
                  "client_id": "CID", "username": "alice", "user_id": "4242"}
        record.update(over)
        self.x.access["OLD-AT"] = True
        self.x.refresh["OLD-RT"] = True
        xauth.save_login(self.paths, record)


class PkceTests(unittest.TestCase):
    def test_s256_challenge_matches_the_verifier(self):
        verifier, challenge = xauth.pkce_pair()
        self.assertRegex(verifier, r"^[A-Za-z0-9_-]{43,128}$")
        digest = hashlib.sha256(verifier.encode()).digest()
        self.assertEqual(challenge, base64.urlsafe_b64encode(digest).rstrip(b"=").decode())
        self.assertNotIn("=", challenge)
        self.assertNotEqual(xauth.pkce_pair()[0], verifier)

    def test_rfc7636_example_vector(self):
        digest = hashlib.sha256(b"dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk").digest()
        self.assertEqual(base64.urlsafe_b64encode(digest).rstrip(b"=").decode(),
                         "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM")

    def test_authorize_url(self):
        url = xauth.authorize_url("CID", "http://127.0.0.1:8766/callback", "st", "ch")
        parts = urllib.parse.urlsplit(url)
        self.assertEqual(f"{parts.scheme}://{parts.netloc}{parts.path}", "https://x.com/i/oauth2/authorize")
        q = {k: v[0] for k, v in urllib.parse.parse_qs(parts.query).items()}
        self.assertEqual(q, {"response_type": "code", "client_id": "CID", "redirect_uri": "http://127.0.0.1:8766/callback",
                             "scope": SCOPE, "state": "st", "code_challenge": "ch", "code_challenge_method": "S256"})
        self.assertIn("scope=tweet.read%20users.read", url)  # spaces as %20, as X's examples do


class LoginFlowTests(XCase):
    def test_login_exchanges_the_code_and_saves_user_and_tokens(self):
        info, out, b = self.login()
        self.assertEqual((info["user_id"], info["username"]), ("4242", "alice"))
        self.assertIn("Logged in as @alice (id 4242)", out)
        form = self.x.token_calls[0]["form"]
        self.assertEqual((form["grant_type"], form["code"], form["client_id"]), ("authorization_code", "good-code", "CID"))
        self.assertEqual(form["redirect_uri"], b.query["redirect_uri"])
        self.assertRegex(b.query["redirect_uri"], r"^http://127\.0\.0\.1:\d+/callback$")
        self.assertIsNone(self.x.token_calls[0]["auth"])  # a public client sends no Authorization header
        saved = self.stored()
        self.assertEqual((saved["access_token"], saved["refresh_token"], saved["client_id"], saved["scope"].split()),
                         ("AT1", "RT1", "CID", SCOPE.split()))
        self.assertEqual((saved["user_id"], saved["username"]), ("4242", "alice"))
        self.assertGreater(saved["expires_at"], 0)
        me = [r for r in self.requests if r["path"] == "/2/users/me"][0]
        self.assertEqual(me["headers"]["Authorization"], "Bearer AT1")

    def test_user_id_is_written_into_an_empty_config_key_and_nothing_else_changes(self):
        before = self.paths.config_file.read_text()
        self.login()
        after = self.paths.config_file.read_text()
        self.assertEqual(after, before.replace('user_id = ""', 'user_id = "4242"'))
        self.assertEqual(tomllib.loads(after)["x"]["user_id"], "4242")
        self.assertNotIn("AT1", after)
        self.assertNotIn("RT1", after)

    def test_an_existing_user_id_is_kept_and_a_different_account_is_flagged(self):
        self.paths.config_file.write_text('[x]\nuser_id = "999"\n')
        _, out, _ = self.login(cfg=self.cfg())
        self.assertIn('user_id = "999"', self.paths.config_file.read_text())
        self.assertIn("WARNING: [x] user_id is 999 but this login is 4242", out)

    def test_no_config_file_is_fine(self):
        cfg = self.cfg()
        self.paths.config_file.unlink()
        info, _, _ = self.login(cfg=cfg)
        self.assertEqual(info["user_id"], "4242")
        self.assertFalse(self.paths.config_file.exists())

    def test_tokens_are_not_in_the_database_or_the_output(self):
        _, out, _ = self.login()
        self.assertNotIn("AT1", out)
        self.assertNotIn("RT1", out)
        db_bytes = self.paths.db_file.read_bytes()
        self.assertNotIn(b"AT1", db_bytes)
        self.assertNotIn(b"RT1", db_bytes)
        self.assertEqual(os.environ.get("X_BEARER_TOKEN"), None)
        self.assertNotIn("AT1", json.dumps(dict(os.environ)))

    def test_confidential_client_uses_basic_auth_and_no_client_id_in_the_body(self):
        self.login(secret=SECRET)
        call = self.x.token_calls[0]
        self.assertEqual(call["auth"], "Basic " + base64.b64encode(f"CID:{SECRET}".encode()).decode())
        self.assertNotIn("client_id", call["form"])
        self.assertNotIn(SECRET, self.paths.config_file.read_text() + xauth.token_file(self.paths).read_text())

    def test_the_callback_server_listens_on_loopback_only(self):
        server = xauth.CallbackServer(0, "s")
        self.addCleanup(server.server_close)
        self.assertEqual(server.server_address[0], "127.0.0.1")

    def test_state_mismatch_is_rejected_and_the_real_answer_still_works(self):
        evil = {"code": "good-code", "state": "not-the-state"}
        _, _, b = self.login(evil, good)
        codes = [p[0] for p in b.pages]
        self.assertEqual(codes, [400, 200])
        self.assertEqual(len(self.x.token_calls), 1)  # the forged answer never reached the token endpoint

    def test_state_mismatch_alone_times_out_and_saves_nothing(self):
        with self.assertRaisesRegex(xauth.LoginError, "timed out"):
            self.login({"code": "good-code", "state": "wrong"}, timeout=1.5)
        self.assertEqual(self.x.token_calls, [])
        self.assertFalse(xauth.token_file(self.paths).exists())

    def test_missing_state_is_rejected_too(self):
        with self.assertRaisesRegex(xauth.LoginError, "timed out"):
            self.login({"code": "good-code"}, timeout=1.5)
        self.assertEqual(self.x.token_calls, [])

    def test_pages_do_not_echo_the_query(self):
        marker = "ZZ-REFLECT-<b>-ZZ"
        _, _, b = self.login({"code": "good-code", "state": "bad", "x": marker}, lambda q: {
            "code": "good-code", "state": q["state"], "y": marker})
        for _, page in b.pages:
            self.assertNotIn("ZZ-REFLECT", page)
            self.assertNotIn("good-code", page)
        self.assertIn("You can close this tab", b.pages[-1][1])

    def test_x_error_is_reported_without_saving(self):
        with self.assertRaisesRegex(xauth.LoginError, r"did not authorize.*access_denied"):
            self.login(lambda q: {"error": "access_denied", "state": q["state"]})
        self.assertFalse(xauth.token_file(self.paths).exists())

    def test_other_paths_do_not_end_the_wait(self):
        server = xauth.CallbackServer(0, "s")
        self.addCleanup(server.server_close)
        threading.Thread(target=self._hit, args=(server.port,), daemon=True).start()
        with self.assertRaisesRegex(xauth.LoginError, "timed out"):
            server.wait(1.5)

    def _hit(self, port):
        try:
            NO_PROXY.open(f"http://127.0.0.1:{port}/favicon.ico", timeout=5).read()
        except urllib.error.HTTPError as exc:
            exc.close()

    def test_a_refused_code_exchange_names_the_callback_url(self):
        with self.assertRaisesRegex(xauth.LoginError, "callback URL in the app is exactly http://127.0.0.1"):
            self.login(lambda q: {"code": "wrong-code", "state": q["state"]})
        self.assertFalse(xauth.token_file(self.paths).exists())

    def test_port_in_use_is_explained(self):
        taken = xauth.CallbackServer(0, "s")
        self.addCleanup(taken.server_close)
        with self.assertRaisesRegex(xauth.LoginError, "--port"):
            xauth.run_login(self.cfg(), client_id="CID", port=taken.port, open_browser=lambda u: None, out=lambda s: None)

    def test_tokens_are_never_sent_to_a_plain_http_remote(self):
        cfg = self.cfg(api_base="http://api.example.invalid/2")
        with self.assertRaisesRegex(xauth.LoginError, "https"):
            xauth.run_login(cfg, client_id="CID", port=0, open_browser=lambda u: None, out=lambda s: None)


class FileTests(XCase):
    def test_file_is_0600_from_the_moment_it_exists(self):
        seen = []
        real = os.replace

        def spy(src, dst):
            seen.append(stat.S_IMODE(os.stat(src).st_mode))
            return real(src, dst)
        old = os.umask(0)  # the loosest umask: the file must still not be group/world readable
        self.addCleanup(os.umask, old)
        with mock.patch("os.replace", spy):
            xauth.save_login(self.paths, {"access_token": "AT"})
        self.assertEqual(seen, [0o600])
        self.assertEqual(stat.S_IMODE(xauth.token_file(self.paths).stat().st_mode), 0o600)
        self.assertEqual(stat.S_IMODE(self.paths.data_dir.stat().st_mode) & 0o077, 0)  # a data dir we created

    def test_rewrite_keeps_0600_and_leaves_no_temp_files(self):
        xauth.save_login(self.paths, {"access_token": "AT1"})
        xauth.save_login(self.paths, {"access_token": "AT2"})
        self.assertEqual(stat.S_IMODE(xauth.token_file(self.paths).stat().st_mode), 0o600)
        self.assertEqual(self.stored()["access_token"], "AT2")
        self.assertEqual([p.name for p in self.paths.data_dir.glob(".x_oauth*")], [])

    def test_corrupt_file_is_reported_not_trusted(self):
        xauth.token_file(self.paths).write_text("{nope")
        self.assertIsNone(xauth.token_source(self.cfg()))
        with self.assertRaisesRegex(xauth.LoginError, "login x"):
            xauth.load_login(self.paths)
        self.assertIn("x_login_problem", xauth.status_info(self.cfg()))


class UseTests(XCase):
    def cfg_now(self, now=10_000, **kw):
        return self.cfg(**kw), (lambda: now)

    def test_valid_token_is_used_as_is(self):
        self.write_login(expires_at=20_000)
        cfg, now = self.cfg_now()
        kw = xauth.sync_kwargs(cfg, now)
        self.assertEqual(kw["token"], "OLD-AT")
        self.assertEqual(self.x.token_calls, [])

    def test_expired_token_is_refreshed_and_the_rotated_refresh_token_is_saved(self):
        self.write_login(expires_at=9_990)  # inside the one-minute margin
        cfg, now = self.cfg_now()
        kw = xauth.sync_kwargs(cfg, now)
        self.assertEqual(kw["token"], "AT1")
        form = self.x.token_calls[0]["form"]
        self.assertEqual((form["grant_type"], form["refresh_token"], form["client_id"]), ("refresh_token", "OLD-RT", "CID"))
        saved = self.stored()
        self.assertEqual((saved["access_token"], saved["refresh_token"], saved["expires_at"]), ("AT1", "RT1", 10_000 + 7200))
        self.assertEqual((saved["username"], saved["user_id"], saved["client_id"]), ("alice", "4242", "CID"))
        self.assertEqual(stat.S_IMODE(xauth.token_file(self.paths).stat().st_mode), 0o600)

    def test_confidential_refresh_uses_basic_auth(self):
        self.write_login(expires_at=1)
        cfg = self.cfg(env={"X_CLIENT_SECRET": SECRET})
        xauth.sync_kwargs(cfg, lambda: 10_000)
        self.assertTrue(self.x.token_calls[0]["auth"].startswith("Basic "))
        self.assertNotIn(SECRET, xauth.token_file(self.paths).read_text())

    def test_a_401_refreshes_once_and_retries_through_a_real_sync(self):
        self.write_login(expires_at=20_000)
        self.x.access.pop("OLD-AT")  # the server no longer accepts it although it looks valid
        cfg, now = self.cfg_now()
        kw = xauth.sync_kwargs(cfg, now)
        out = sync.sync(self.db, user_id="4242", api_base=self.api, **kw)
        self.assertEqual((out["inserted"], out["complete"]), (1, True))
        self.assertEqual(len(self.x.token_calls), 1)
        saved = self.stored()
        self.assertEqual((saved["access_token"], saved["refresh_token"]), ("AT1", "RT1"))
        bookmarks = [r for r in self.requests if r["path"].startswith("/2/users/4242/bookmarks")]
        self.assertEqual([r["headers"]["Authorization"] for r in bookmarks], ["Bearer OLD-AT", "Bearer AT1"])

    def test_a_token_that_is_still_refused_after_refreshing_fails_once_not_forever(self):
        self.write_login(expires_at=20_000)
        self.x.access.clear()
        kw = xauth.sync_kwargs(*self.cfg_now())
        self.x.access.pop("AT1", None)
        with mock.patch.object(FakeX, "mint", lambda s: {"access_token": "BAD", "refresh_token": "RT-BAD", "expires_in": 7200}):
            with self.assertRaises(sync.SyncError):
                sync.sync(self.db, user_id="4242", api_base=self.api, **kw)
        self.assertEqual(len(self.x.token_calls), 1)

    def test_a_failed_refresh_says_to_log_in_again_and_keeps_the_file(self):
        self.write_login(expires_at=1)
        self.x.refresh.clear()
        before = xauth.token_file(self.paths).read_text()
        with self.assertRaises(xauth.LoginError) as ctx:
            xauth.sync_kwargs(self.cfg(), lambda: 10_000)
        self.assertIn("unhoard login x", str(ctx.exception))
        self.assertIsInstance(ctx.exception, sync.SyncError)
        self.assertEqual(xauth.token_file(self.paths).read_text(), before)

    def test_a_temporary_failure_does_not_tell_the_user_to_log_in_again(self):
        self.write_login(expires_at=1)
        self.x.fail_refresh = (503, {"title": "Service Unavailable"})
        with self.assertRaisesRegex(xauth.LoginError, "try again later") as ctx:
            xauth.sync_kwargs(self.cfg(), lambda: 10_000)
        self.assertNotIn("login x", str(ctx.exception))

    def test_no_refresh_token_and_expired_asks_for_a_new_login(self):
        self.write_login(expires_at=1, refresh_token="")
        with self.assertRaisesRegex(xauth.LoginError, "login x"):
            xauth.sync_kwargs(self.cfg(), lambda: 10_000)

    def test_cli_sync_reports_a_failed_refresh(self):
        self.write_login(expires_at=1)
        self.x.refresh.clear()
        env = {"UNHOARD_HOME": str(self.paths.data_dir), "UNHOARD_CONFIG": str(self.paths.config_file)}
        self.paths.config_file.write_text(f'[x]\napi_base = "{self.api}"\n')
        err = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            os.environ.pop("X_BEARER_TOKEN", None)
            code = cli.main(["sync"])
        self.assertEqual(code, 1)
        self.assertIn("sync failed", err.getvalue())
        self.assertIn("login x", err.getvalue())

    def test_two_overlapping_refreshes_use_the_refresh_token_once(self):
        self.write_login(expires_at=1)
        a = xauth.StoredLogin(self.cfg(), lambda: 10_000)
        b = xauth.StoredLogin(self.cfg(), lambda: 10_000)
        first = a.access_token()
        second = b.access_token()  # reads the file the first one already refreshed
        self.assertEqual((first, second), ("AT1", "AT1"))
        self.assertEqual(len(self.x.token_calls), 1)


class PrecedenceTests(XCase):
    def test_env_then_config_then_login(self):
        self.write_login(expires_at=4_000_000_000)
        cfg = self.cfg(env={"X_BEARER_TOKEN": "ENV-TOKEN"}, bearer_token="CONFIG-TOKEN")
        self.assertEqual((xauth.token_source(cfg), xauth.sync_kwargs(cfg)["token"]), ("env", "ENV-TOKEN"))
        cfg = self.cfg(bearer_token="CONFIG-TOKEN")
        self.assertEqual((xauth.token_source(cfg), xauth.sync_kwargs(cfg)["token"]), ("config", "CONFIG-TOKEN"))
        self.assertNotIn("fetch", xauth.sync_kwargs(cfg))  # the manual tokens keep the plain fetch
        cfg = self.cfg()
        self.assertEqual((xauth.token_source(cfg), xauth.sync_kwargs(cfg)["token"]), ("login", "OLD-AT"))
        xauth.token_file(self.paths).unlink()
        self.assertEqual((xauth.token_source(cfg), xauth.sync_kwargs(cfg)), (None, {"token": ""}))
        with self.assertRaisesRegex(sync.SyncError, "login x"):
            sync.sync(self.db, **xauth.sync_kwargs(cfg), api_base=self.api)


class LogoutTests(XCase):
    def test_logout_revokes_and_deletes(self):
        self.write_login(expires_at=20_000)
        lines = []
        self.assertEqual(xauth.run_logout(self.cfg(), lines.append), 0)
        self.assertFalse(xauth.token_file(self.paths).exists())
        self.assertCountEqual(self.x.revoked, ["OLD-RT", "OLD-AT"])
        self.assertIn("Logged out", "\n".join(lines))

    def test_logout_deletes_even_when_the_revoke_fails(self):
        self.write_login(expires_at=20_000)
        self.x.fail_revoke = True
        lines = []
        self.assertEqual(xauth.run_logout(self.cfg(), lines.append), 0)
        self.assertFalse(xauth.token_file(self.paths).exists())
        self.assertIn("Could not revoke", "\n".join(lines))

    def test_logout_deletes_a_corrupt_file_and_says_so_when_there_is_nothing(self):
        xauth.token_file(self.paths).write_text("junk")
        self.assertEqual(xauth.run_logout(self.cfg(), lambda s: None), 0)
        self.assertFalse(xauth.token_file(self.paths).exists())
        lines = []
        self.assertEqual(xauth.run_logout(self.cfg(), lines.append), 0)
        self.assertEqual(lines, ["No saved X login."])


class RedirectTests(XCase):
    """A redirect is never followed, so a token is never copied to another address."""

    def bounce(self):
        seen = []
        target_ctx = helpers.fake_http(lambda h, b: (seen.append(dict(h.headers)) or (200, {"data": []})))
        target, target_reqs = target_ctx.__enter__()
        self.addCleanup(target_ctx.__exit__, None, None, None)
        bounce_ctx = helpers.fake_http(lambda h, b: (302, {}, {"Location": target + "/stolen"}))
        base, reqs = bounce_ctx.__enter__()
        self.addCleanup(bounce_ctx.__exit__, None, None, None)
        return base, reqs, target_reqs

    def test_the_login_calls_do_not_follow_a_redirect(self):
        base, reqs, target_reqs = self.bounce()
        status, _ = xauth._call(base + "/2/users/me", bearer="SECRET-AT")
        self.assertEqual(status, 302)
        status, _ = xauth._call(base + "/2/oauth2/token", client_id="CID", client_secret="SECRET-CS", fields={"a": "b"})
        self.assertEqual(status, 302)
        self.assertEqual(len(reqs), 2)
        self.assertEqual(target_reqs, [])  # neither the bearer token nor the client secret went anywhere else

    def test_sync_does_not_follow_a_redirect_with_the_token(self):
        base, reqs, target_reqs = self.bounce()
        with self.assertRaisesRegex(sync.SyncError, "HTTP 302"):
            sync.fetch_json(base + "/2/users/me", "SECRET-AT")
        self.assertEqual(target_reqs, [])


class LockTests(XCase):
    def test_logout_waits_for_a_refresh_and_revokes_what_the_refresh_saved(self):
        self.write_login(expires_at=20_000)
        lines, done = [], []
        with xauth._locked(self.paths):  # a refresh in another process holds the lock ...
            thread = threading.Thread(target=lambda: done.append(xauth.run_logout(self.cfg(), lines.append)), daemon=True)
            thread.start()
            thread.join(0.4)
            self.assertTrue(thread.is_alive())  # ... so the logout is waiting, and has revoked nothing yet
            self.assertEqual(self.x.revoked, [])
            xauth.save_login(self.paths, {"access_token": "NEW-AT", "refresh_token": "NEW-RT", "expires_at": 20_000,
                                          "client_id": "CID", "scope": SCOPE})  # ... and rotates the tokens
        thread.join(10)
        self.assertEqual(done, [0])
        self.assertCountEqual(self.x.revoked, ["NEW-RT", "NEW-AT"])  # the current tokens, not the stale ones
        self.assertFalse(xauth.token_file(self.paths).exists())  # and nothing can write the file back afterwards

    def test_login_saves_under_the_lock(self):
        taken = []
        real = xauth._locked

        @contextlib.contextmanager
        def spy(paths):
            with real(paths):
                taken.append(1)
                yield
        with mock.patch.object(xauth, "_locked", spy):
            self.login()
        self.assertGreaterEqual(len(taken), 2)  # the save, and the user fields added after /users/me

    def test_a_new_login_signs_the_previous_one_out_before_the_new_authorization_starts(self):
        self.write_login(expires_at=20_000)
        seen = {}
        real_browser = browser(self.x, good)

        def open_browser(url):
            seen["file_gone"] = not xauth.token_file(self.paths).exists()
            seen["revoked"] = list(self.x.revoked)
            seen["token_calls"] = len(self.x.token_calls)
            real_browser(url)

        open_browser.pages, open_browser.thread = real_browser.pages, None
        lines = []
        info = None
        with mock.patch.object(xauth, "webbrowser", mock.Mock(open=open_browser)):
            info = xauth.run_login(self.cfg(), client_id="CID", port=0, timeout=10, out=lines.append, replace=True)
        real_browser.thread.join(10)
        # by the time the browser opens, the old login is revoked and deleted: a per-grant revoke cannot hit new tokens
        self.assertEqual(seen["file_gone"], True)
        self.assertCountEqual(seen["revoked"], ["OLD-RT", "OLD-AT"])
        self.assertEqual(seen["token_calls"], 0)
        self.assertCountEqual(self.x.revoked, ["OLD-RT", "OLD-AT"])  # and nothing is revoked afterwards
        self.assertEqual(self.stored()["access_token"], "AT1")
        out = "\n".join(lines)
        self.assertIn("Signing the saved X login out first", out)
        self.assertEqual(info["username"], "alice")

    def test_without_a_terminal_and_without_replace_a_saved_login_is_never_signed_out(self):
        self.write_login(expires_at=20_000)
        opened = []
        with self.assertRaisesRegex(xauth.LoginError, "--replace"):
            xauth.run_login(self.cfg(), client_id="CID", port=0, timeout=1, open_browser=opened.append,
                            out=lambda s: None)
        self.assertEqual(opened, [])
        self.assertEqual(self.stored()["access_token"], "OLD-AT")  # still there
        self.assertEqual(self.x.revoked, [])  # and not revoked at X

    def test_a_terminal_asks_and_no_keeps_the_saved_login(self):
        self.write_login(expires_at=20_000)
        asked = []
        with self.assertRaises(xauth.LoginDeclined):
            xauth.run_login(self.cfg(), client_id="CID", port=0, timeout=1, open_browser=lambda u: None,
                            out=lambda s: None, ask=lambda q: asked.append(q) or False)
        self.assertEqual(len(asked), 1)
        self.assertIn("Sign it out", asked[0])
        self.assertEqual(self.stored()["access_token"], "OLD-AT")
        self.assertEqual(self.x.revoked, [])

    def test_a_terminal_asks_and_yes_signs_out_then_logs_in(self):
        self.write_login(expires_at=20_000)
        _, out, _ = self.login(ask=lambda q: True)
        self.assertCountEqual(self.x.revoked, ["OLD-RT", "OLD-AT"])
        self.assertEqual(self.stored()["access_token"], "AT1")
        self.assertIn("Signing the saved X login out first", out)

    def test_replace_skips_the_question_and_no_question_is_asked_without_a_saved_login(self):
        def never(_q):
            raise AssertionError("must not ask")

        self.write_login(expires_at=20_000)
        self.login(replace=True, ask=never)
        self.assertEqual(self.stored()["access_token"], "AT1")
        xauth.token_file(self.paths).unlink()
        self.x.revoked.clear()
        self.login(ask=never)  # nothing to sign out, so nothing to ask
        self.assertEqual(self.x.revoked, [])

    def test_the_prompt_reads_y_or_yes_and_anything_else_is_no(self):
        for answer, expected in (("y", True), ("YES", True), (" y ", True), ("", False), ("n", False), ("maybe", False)):
            with mock.patch("builtins.input", return_value=answer):
                self.assertIs(xauth._ask_yes_no("q?"), expected, answer)
        with mock.patch("builtins.input", side_effect=EOFError):
            self.assertIs(xauth._ask_yes_no("q?"), False)

    def test_a_failed_revoke_of_the_previous_login_is_reported_and_the_login_still_works(self):
        self.write_login(expires_at=20_000)
        self.x.fail_revoke = True
        _, out, _ = self.login(replace=True)
        self.assertIn("Could not revoke", out)
        self.assertEqual(self.stored()["access_token"], "AT1")

    def test_the_old_login_stays_when_the_callback_port_is_busy(self):
        self.write_login(expires_at=20_000)
        blocker = xauth.CallbackServer(0, "s")
        self.addCleanup(blocker.server_close)
        with self.assertRaisesRegex(xauth.LoginError, "cannot listen"):
            xauth.run_login(self.cfg(), client_id="CID", port=blocker.port, timeout=1, out=lambda s: None)
        self.assertEqual(self.stored()["access_token"], "OLD-AT")
        self.assertEqual(self.x.revoked, [])


class EdgeTests(XCase):
    def test_status_info_survives_the_file_vanishing_after_it_was_read(self):
        data = {"access_token": "AT", "expires_at": 20_000, "scope": SCOPE}
        with mock.patch.object(xauth, "load_login", return_value=data):  # read fine, but there is no file to stat
            info = xauth.status_info(self.cfg(), now=lambda: 100)
        self.assertIsNone(info["x_login"])
        self.assertEqual(xauth.describe_source(info)[0], "warn")  # reads as "no login"

    def test_a_redirect_with_the_right_state_but_no_code_is_a_clear_local_error(self):
        with self.assertRaisesRegex(xauth.LoginError, "no authorization code.*nothing was saved"):
            self.login(lambda q: {"state": q["state"]})
        self.assertEqual(self.x.token_calls, [])
        self.assertFalse(xauth.token_file(self.paths).exists())


class CliCase(XCase):
    def setUp(self):
        super().setUp()
        home = self.tmp / "home"
        home.mkdir()
        env = {"UNHOARD_HOME": str(self.paths.data_dir), "UNHOARD_CONFIG": str(self.paths.config_file),
               "HOME": str(home)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        dead = mock.patch.object(omnigent_detect, "DEFAULT_API", "http://127.0.0.1:9")  # never the real server here
        dead.start()
        self.addCleanup(dead.stop)
        for name in ("X_BEARER_TOKEN", "X_CLIENT_ID", "X_CLIENT_SECRET", "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
            os.environ.pop(name, None)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class SetupStepsTests(CliCase):
    def test_missing_client_id_prints_exact_steps_and_starts_nothing(self):
        with mock.patch.object(xauth, "CallbackServer", side_effect=AssertionError("must not listen")):
            code, out, _ = self.run_cli("login", "x")
        self.assertEqual(code, 1)
        for needle in ("http://127.0.0.1:8766/callback", "OAuth 2.0", "Native App", SCOPE, "X_CLIENT_ID", "X_CLIENT_SECRET"):
            self.assertIn(needle, out)
        code, out, _ = self.run_cli("login", "x", "--port", "9123")
        self.assertIn("http://127.0.0.1:9123/callback", out)
        self.assertNotIn("8766", out)

    def test_client_id_sources(self):
        seen = []
        with mock.patch.object(xauth, "run_login", lambda cfg, **kw: seen.append(kw)):
            self.paths.config_file.write_text('[x]\nclient_id = "FROM-CONFIG"\n')
            self.run_cli("login", "x")
            os.environ["X_CLIENT_ID"] = "FROM-ENV"
            self.run_cli("login", "x")
            self.run_cli("login", "x", "--client-id", "FROM-FLAG", "--port", "9000")
        self.assertEqual([k["client_id"] for k in seen], ["FROM-CONFIG", "FROM-ENV", "FROM-FLAG"])
        self.assertEqual(seen[2]["port"], 9000)

    def test_login_with_a_saved_login_and_no_terminal_refuses_unless_replace(self):
        self.write_login(expires_at=20_000)
        with mock.patch.object(xauth, "_has_terminal", return_value=False), \
                mock.patch.object(xauth.webbrowser, "open", side_effect=AssertionError("must not open")):
            code, _, err = self.run_cli("login", "x", "--client-id", "CID", "--port", "0", "--timeout", "1")
        self.assertEqual(code, 1)
        self.assertIn("--replace", err)
        self.assertEqual(self.stored()["access_token"], "OLD-AT")
        self.assertEqual(self.x.revoked, [])

    def test_login_replace_flag_reaches_the_flow_and_a_terminal_gets_the_prompt(self):
        seen = []
        with mock.patch.object(xauth, "run_login", lambda cfg, **kw: seen.append(kw)), \
                mock.patch.object(xauth, "_has_terminal", return_value=False):
            self.run_cli("login", "x", "--client-id", "CID")
            self.run_cli("login", "x", "--client-id", "CID", "--replace")
        self.assertEqual([(k["replace"], k["ask"]) for k in seen], [(False, None), (True, None)])
        seen.clear()
        with mock.patch.object(xauth, "run_login", lambda cfg, **kw: seen.append(kw)), \
                mock.patch.object(xauth, "_has_terminal", return_value=True):
            self.run_cli("login", "x", "--client-id", "CID")
        self.assertIs(seen[0]["ask"], xauth._ask_yes_no)

    def test_declining_the_prompt_exits_1_with_a_plain_message_and_keeps_the_login(self):
        self.write_login(expires_at=20_000)
        with mock.patch.object(xauth, "_has_terminal", return_value=True), \
                mock.patch("builtins.input", return_value="n"):
            code, _, err = self.run_cli("login", "x", "--client-id", "CID", "--port", "0", "--timeout", "1")
        self.assertEqual(code, 1)
        self.assertIn("Cancelled", err)
        self.assertNotIn("login failed", err)
        self.assertEqual(self.stored()["access_token"], "OLD-AT")

    def test_login_failure_exits_1_with_the_reason(self):
        with mock.patch.object(xauth, "run_login", side_effect=xauth.LoginError("boom")):
            code, _, err = self.run_cli("login", "x", "--client-id", "CID")
        self.assertEqual((code, err.strip()), (1, "login failed: boom"))

    def test_login_end_to_end_through_the_cli_then_logout(self):
        self.paths.config_file.write_text(f'[x]\nuser_id = ""\napi_base = "{self.api}"\n')
        b = browser(self.x, good)
        with mock.patch.object(xauth.webbrowser, "open", b):
            code, out, _ = self.run_cli("login", "x", "--client-id", "CID", "--port", "0", "--timeout", "10")
        self.assertEqual(code, 0, out)
        self.assertIn("Logged in as @alice (id 4242)", out)
        self.assertEqual(tomllib.loads(self.paths.config_file.read_text())["x"]["user_id"], "4242")
        code, out, _ = self.run_cli("logout", "x")
        self.assertEqual(code, 0)
        self.assertFalse(xauth.token_file(self.paths).exists())

    def test_no_browser_does_not_open_one(self):
        with mock.patch.object(xauth.webbrowser, "open", side_effect=AssertionError("opened")):
            code, out, err = self.run_cli("login", "x", "--client-id", "CID", "--port", "0", "--timeout", "1",
                                          "--no-browser")
        self.assertEqual(code, 1)
        self.assertIn("ssh -L", out)  # the port-forwarding hint for a headless machine
        self.assertIn("https://x.com/i/oauth2/authorize?", out)
        self.assertIn("timed out", err)


class NeverLeakTests(CliCase):
    def test_status_and_doctor_show_the_source_but_never_a_token(self):
        self.write_login(expires_at=4_000_000_000, access_token="AT-SECRET-VALUE", refresh_token="RT-SECRET-VALUE")
        os.environ["X_CLIENT_SECRET"] = SECRET
        self.paths.config_file.write_text(f'[x]\napi_base = "{self.api}"\n[enrich]\nagent = ""\n')
        code, out, _ = self.run_cli("status")
        info = json.loads(out)
        self.assertEqual((code, info["x_token_set"], info["x_token_source"]), (0, True, "login"))
        self.assertEqual((info["x_login"]["user"], info["x_login"]["expired"], info["x_login"]["file_mode"]),
                         ("@alice", False, "600"))
        self.assertTrue(info["x_login"]["expires_at"].startswith("2096-"))
        cfg = Config.load()
        rendered = doctor.render(doctor.run_doctor(cfg, run=lambda *a: (0, "", "")))
        self.assertRegex(rendered, r"X token\s+PASS\s+saved login @alice via `login x`; access token valid until 2096-")
        for text in (out, rendered):
            for secret in ("AT-SECRET-VALUE", "RT-SECRET-VALUE", SECRET):
                self.assertNotIn(secret, text)

    def test_status_names_env_and_config_sources_and_a_fresh_install_has_none(self):
        self.assertEqual(json.loads(self.run_cli("status")[1])["x_token_source"], None)
        self.assertFalse(json.loads(self.run_cli("status")[1])["x_token_set"])
        self.paths.config_file.write_text('[x]\nbearer_token = "CFG-SECRET"\n')
        out = self.run_cli("status")[1]
        self.assertEqual(json.loads(out)["x_token_source"], "config")
        os.environ["X_BEARER_TOKEN"] = "ENV-SECRET"
        out = self.run_cli("status")[1]
        self.assertEqual(json.loads(out)["x_token_source"], "env")
        self.assertNotIn("ENV-SECRET", out)
        self.assertNotIn("CFG-SECRET", out)

    def test_doctor_warns_about_a_loose_token_file_mode(self):
        self.write_login(expires_at=4_000_000_000)
        os.chmod(xauth.token_file(self.paths), 0o644)
        status, detail = xauth.describe_source(xauth.status_info(self.cfg()))
        self.assertEqual(status, "warn")
        self.assertIn("chmod 600", detail)

    def test_agent_and_worker_environments_have_no_token_and_no_client_secret(self):
        base = {"X_BEARER_TOKEN": "T", "X_CLIENT_SECRET": SECRET, "X_CLIENT_ID": "CID", "KEEP": "1"}
        env = agents.agent_env(base, ["codex"], "X_BEARER_TOKEN")
        self.assertEqual(env, {"X_CLIENT_ID": "CID", "KEEP": "1"})
        self.assertNotIn("X_CLIENT_SECRET", worker_env(base, "BMK-1"))
        self.assertNotIn("X_BEARER_TOKEN", worker_env(base, "BMK-1"))

    def test_the_stored_token_is_never_put_into_the_environment(self):
        self.write_login(expires_at=1)
        before = dict(os.environ)
        kw = xauth.sync_kwargs(self.cfg(), lambda: 10_000)
        self.assertEqual(dict(os.environ), before)
        self.assertNotIn(kw["token"], json.dumps(dict(os.environ)))
        # and nothing outside xauth reads the token file
        src = Path(xauth.__file__).parent
        readers = [p.name for p in src.rglob("*.py") if p.name != "xauth.py" and "x_oauth" in p.read_text()]
        self.assertEqual(readers, [])


class TomlStringTests(unittest.TestCase):
    def test_set_strings_replaces_adds_and_keeps_comments(self):
        text = '# top\n[x]\nuser_id = ""  # id\nmax_items = 5\n\n[other]\na = 1\n'
        out = tomledit.set_strings(text, "x", {"user_id": '12"3'})
        self.assertEqual(tomllib.loads(out)["x"], {"user_id": '12"3', "max_items": 5})
        self.assertIn("# top", out)
        self.assertEqual(tomllib.loads(tomledit.set_strings("[a]\nb = 1\n", "x", {"user_id": "7"}))["x"], {"user_id": "7"})
        added = tomledit.set_strings("[x]\nmax_items = 5\n", "x", {"user_id": "7"})
        self.assertEqual(tomllib.loads(added)["x"], {"max_items": 5, "user_id": "7"})

    def test_lists_still_work(self):
        out = tomledit.set_lists("[c]\nfolders = []\n", "c", {"folders": ["a", "b"]})
        self.assertEqual(tomllib.loads(out)["c"]["folders"], ["a", "b"])


if __name__ == "__main__":
    unittest.main()
