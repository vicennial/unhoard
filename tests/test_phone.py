"""Phone access: [server] allowed_hosts (Host and Origin checks) and the ``unhoard phone`` command.

Names here are placeholders (mybox.tail1234.ts.net). The tailscale binary is never run: its output is stubbed.
"""
import helpers
import contextlib
import http.client
import io
import json
import os
import stat
import subprocess
import threading
import tomllib
import unittest
from types import SimpleNamespace
from unittest import mock

from unhoard import cli, doctor, phone
from unhoard.config import Config, check_allowed_host
from unhoard.server import BookmarksServer
from unhoard.store import connect

NAME = "mybox.tail1234.ts.net"


class HostRuleTests(unittest.TestCase):
    def test_valid_names_are_lower_cased(self):
        for given, expected in ((NAME, NAME), ("MyBox.Tail1234.TS.net", NAME), ("  mybox  ", "mybox"),
                                ("a-b.example.com", "a-b.example.com"), ("x1.example", "x1.example")):
            self.assertEqual(check_allowed_host(given), (expected, None), given)

    def test_bad_entries_are_refused_with_a_reason(self):
        for bad in ("*", "*.ts.net", "mybox.*", "https://mybox.ts.net", "mybox.ts.net/", "mybox.ts.net/path",
                    "mybox.ts.net:8765", "user@mybox.ts.net", "mybox.ts.net.", "", "   ", "192.168.1.5", "100.64.0.1",
                    "10.0.0.1:8765", "[::1]", "::1", "-bad.example", "bad-.example", "a..b", "my box.example",
                    "mybox.ts.net?x=1", "mybox.ts.net#frag", "a" * 64 + ".example", 5, None, ["x"]):
            name, why = check_allowed_host(bad)
            self.assertIsNone(name, repr(bad))
            self.assertTrue(why, repr(bad))

    def test_the_config_lists_the_rule_in_its_problems_and_ignores_bad_entries(self):
        cfg = Config({"server": {"allowed_hosts": [NAME, "*", "https://evil.example", "10.0.0.5", NAME.upper()]}},
                     helpers.Paths(helpers.Path("/x/d"), helpers.Path("/x/c.toml")), env={})
        self.assertEqual(cfg.allowed_hosts(), [NAME])  # valid ones only, lower-cased, no duplicates
        problems = [p for p in cfg.problems if "allowed_hosts" in p]
        self.assertEqual(len(problems), 3)
        for p in problems:
            self.assertIn("host name such as mybox.tail1234.ts.net", p)  # the rule is spelled out
        self.assertIn("wildcards are not allowed", problems[0])
        self.assertIn("IP addresses are not accepted", problems[2])

    def test_default_is_empty_and_a_wrong_type_is_a_problem(self):
        paths = helpers.Paths(helpers.Path("/x/d"), helpers.Path("/x/c.toml"))
        self.assertEqual(Config({}, paths, env={}).allowed_hosts(), [])
        self.assertEqual(Config({}, paths, env={}).problems, [])
        bad = Config({"server": {"allowed_hosts": "mybox.ts.net"}}, paths, env={})
        self.assertEqual(bad.allowed_hosts(), [])
        self.assertIn("[server] allowed_hosts must be list", bad.problems)


class Served(helpers.TempCase):
    def serve(self, hosts, **server_settings):
        cfg = Config({"server": {"allowed_hosts": hosts, **server_settings}}, self.paths, env={})
        if not self.db.execute("SELECT count(*) FROM bookmarks").fetchone()[0]:
            helpers.seed_basic(self.db)
        server = BookmarksServer(0, lambda: connect(self.paths), lambda c, n: {"ref": "x", "status": "started"}, 3, 3,
                                 True, cfg)
        server.quiet = True
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.addCleanup(stop)
        self.port = server.server_port
        self.server = server

    LOGIN = "someone@example.invalid"  # a stand-in for the Tailscale-User-Login value that `tailscale serve` adds

    def call(self, method, path, host=None, origin=None, body=None, login="default", raw=False):
        """Status code (or ``(status, body)`` when ``raw``). A request for a non-loopback ``host`` carries the identity
        header unless ``login`` is None (or empty), as `tailscale serve` would send it."""
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host if host is not None else f"127.0.0.1:{self.port}")
        if origin is not None:
            conn.putheader("Origin", origin)
        if login == "default":
            login = self.LOGIN if host is not None else None
        if login is not None:
            conn.putheader("Tailscale-User-Login", login)
        data = json.dumps(body).encode() if body is not None else b""
        if method == "POST":
            conn.putheader("X-Unhoard", "1")
            conn.putheader("Content-Type", "application/json")
        conn.putheader("Content-Length", str(len(data)))
        conn.endheaders(data)
        resp = conn.getresponse()
        payload = resp.read()
        return (resp.status, payload) if raw else resp.status


class HostAndOriginTests(Served):
    def test_default_is_loopback_only(self):
        self.serve([])
        self.assertEqual(self.call("GET", "/api/cards"), 200)
        self.assertEqual(self.call("GET", "/api/cards", host=f"localhost:{self.port}"), 200)
        for host in (NAME, f"{NAME}:443", "evil.example"):
            self.assertEqual(self.call("GET", "/api/cards", host=host), 403, host)

    def test_an_allowed_host_passes_with_or_without_a_proxy_port_in_any_case(self):
        self.serve([NAME])
        for host in (NAME, f"{NAME}:443", f"{NAME}:8443", NAME.upper(), "MyBox.Tail1234.Ts.Net:443"):
            self.assertEqual(self.call("GET", "/api/cards", host=host), 200, host)
            self.assertEqual(self.call("GET", "/", host=host), 200, host)
        self.assertEqual(self.call("GET", "/api/cards"), 200)  # loopback still works, and needs no identity header

    def test_an_allowed_name_with_the_servers_own_port_is_refused(self):
        # a DNS-rebinding page is served from http://NAME:PORT, so its Host carries the server's own port
        self.serve([NAME])
        own = f"{NAME}:{self.port}"
        self.assertEqual(self.call("GET", "/api/cards", host=own), 403)
        self.assertEqual(self.call("GET", "/", host=own), 403)
        self.assertEqual(self.call("GET", "/api/cards", host=own, origin=f"http://{own}"), 403)
        self.assertEqual(self.call("POST", "/api/cards/BMK-0000000B/decide", host=own, origin=f"http://{own}",
                                   body={"decision": "skip", "note": "from a rebinding page"}), 403)
        status = self.db.execute("SELECT status FROM actions WHERE action_id='BMK-0000000B'").fetchone()[0]
        self.assertEqual(status, "proposed")  # and nothing was decided

    def test_a_bare_name_means_port_80_so_it_is_refused_if_the_server_itself_is_on_80(self):
        self.serve([NAME])
        self.server.server_port = 80
        self.assertEqual(self.call("GET", "/api/cards", host=NAME), 403)
        self.assertEqual(self.call("GET", "/api/cards", host=f"{NAME}:443"), 200)

    def test_other_hosts_still_fail(self):
        self.serve([NAME])
        for host in ("evil.example", f"{NAME}.evil.example", f"evil.{NAME}", "tail1234.ts.net", f"{NAME}@evil.example",
                     f"{NAME}:", f"{NAME}:99999x", f"{NAME} ", "", "127.0.0.1", "[::1]:8765", f"{NAME}:443:1"):
            self.assertEqual(self.call("GET", "/api/cards", host=host), 403, repr(host))

    def test_only_https_origins_pass_for_an_allowed_host(self):
        self.serve([NAME])
        for origin in (f"https://{NAME}", f"https://{NAME}:443", f"https://{NAME}:8443", f"https://{NAME.upper()}"):
            self.assertEqual(self.call("GET", "/api/cards", host=NAME, origin=origin), 200, origin)
        for origin in (f"http://{NAME}", f"http://{NAME}:8765", f"http://{NAME}:{self.port}", f"https://{NAME}:{self.port}",
                       "https://evil.example", f"https://{NAME}.evil.example", f"ftp://{NAME}", f"https://{NAME}/x",
                       f"https://user@{NAME}", "null", f"https://{NAME}:abc", "https://"):
            self.assertEqual(self.call("GET", "/api/cards", host=NAME, origin=origin), 403, origin)

    def test_an_allowed_names_origin_is_not_accepted_on_a_loopback_host(self):
        self.serve([NAME])
        self.assertEqual(self.call("GET", "/api/cards", origin=f"https://{NAME}"), 403)

    def test_a_request_by_an_allowed_name_needs_the_tailscale_identity_header(self):
        self.serve([NAME])
        for login in (None, ""):
            self.assertEqual(self.call("GET", "/api/cards", host=NAME, login=login), 403, repr(login))
            self.assertEqual(self.call("GET", "/", host=NAME, origin=f"https://{NAME}", login=login), 403)
            self.assertEqual(self.call("POST", "/api/cards/BMK-0000000B/decide", host=NAME, origin=f"https://{NAME}",
                                       body={"decision": "skip"}, login=login), 403)
        self.assertEqual(self.call("GET", "/api/cards", host=NAME, login="x@example.invalid"), 200)

    def test_loopback_needs_no_identity_header(self):
        self.serve([NAME])
        self.assertEqual(self.call("GET", "/api/cards", login=None), 200)

    def test_the_identity_requirement_can_be_turned_off(self):
        self.serve([NAME], require_tailscale_identity=False)
        self.assertEqual(self.call("GET", "/api/cards", host=NAME, login=None), 200)
        self.assertEqual(self.call("POST", "/api/cards/BMK-0000000B/decide", host=NAME, origin=f"https://{NAME}",
                                   body={"decision": "skip"}, login=None), 200)
        # only that check is off: the own-port and http-origin rules still hold
        self.assertEqual(self.call("GET", "/api/cards", host=f"{NAME}:{self.port}", login=None), 403)
        self.assertEqual(self.call("GET", "/api/cards", host=NAME, origin=f"http://{NAME}", login=None), 403)

    def test_a_non_boolean_setting_does_not_open_the_check(self):
        self.serve([NAME], require_tailscale_identity="false")  # a string: the config check reports it, the server fails closed
        self.assertEqual(self.call("GET", "/api/cards", host=NAME, login=None), 403)

    def test_the_identity_value_is_never_logged_or_echoed(self):
        self.serve([NAME])
        self.server.quiet = False
        secret = "private.person@example.invalid"
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            ok = self.call("GET", "/api/cards", host=NAME, login=secret, raw=True)
            bad = self.call("GET", "/api/cards", host=f"{NAME}:{self.port}", login=secret, raw=True)
        self.assertEqual((ok[0], bad[0]), (200, 403))
        self.assertNotIn(secret, err.getvalue())
        self.assertNotIn(secret.encode(), bad[1])
        self.assertNotIn(secret.encode(), ok[1])

    def test_an_origin_for_an_unlisted_host_fails_even_with_a_good_host_header(self):
        self.serve([NAME])
        self.assertEqual(self.call("GET", "/api/cards", host=NAME, origin="https://other.example"), 403)
        self.serve([])
        self.assertEqual(self.call("GET", "/api/cards", origin=f"https://{NAME}"), 403)

    def test_posts_use_the_same_host_and_origin_rules_and_still_need_the_header(self):
        self.serve([NAME])
        body = {"decision": "skip"}
        self.assertEqual(self.call("POST", "/api/cards/BMK-0000000A/decide", host=NAME, origin=f"https://{NAME}",
                                   body=body), 200)
        self.assertEqual(self.call("POST", "/api/cards/BMK-0000000B/decide", host=NAME, origin="https://evil.example",
                                   body=body), 403)
        self.assertEqual(self.call("POST", "/api/cards/BMK-0000000B/decide", host="evil.example", origin=f"https://{NAME}",
                                   body=body), 403)
        # no custom header: refused as before
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("POST", "/api/cards/BMK-0000000B/decide", body=json.dumps(body),
                     headers={"Host": NAME, "Origin": f"https://{NAME}", "Content-Type": "application/json"})
        self.assertEqual(conn.getresponse().status, 403)

    def test_the_socket_is_still_bound_to_loopback_only(self):
        self.serve([NAME])
        self.assertEqual(self.server.server_address[0], "127.0.0.1")  # an allowed host adds no listening address
        from unhoard import server as server_module
        self.assertEqual(server_module.BIND, "127.0.0.1")


TS_JSON = json.dumps({"BackendState": "Running", "Self": {"DNSName": NAME.upper() + ".", "HostName": "mybox"}})


def fake_run(stdout=TS_JSON, code=0, stderr="", raises=None):
    calls = []

    def run(argv, **kw):
        calls.append(argv)
        if raises:
            raise raises
        return SimpleNamespace(returncode=code, stdout=stdout, stderr=stderr)
    run.calls = calls
    return run


class TailscaleInfoTests(unittest.TestCase):
    def test_reads_the_magicdns_name_and_strips_the_trailing_dot(self):
        run = fake_run()
        info = phone.tailscale_info(which=lambda n: "/usr/bin/tailscale", run=run)
        self.assertEqual((info["installed"], info["name"], info["state"]), (True, NAME, "Running"))
        self.assertEqual(run.calls, [["/usr/bin/tailscale", "status", "--json"]])  # read-only: only `status`

    def test_not_installed(self):
        run = fake_run()
        info = phone.tailscale_info(which=lambda n: None, run=run)
        self.assertEqual((info["installed"], info["name"]), (False, None))
        self.assertEqual(run.calls, [])  # nothing was run

    def test_failures_are_notes_not_crashes(self):
        for run in (fake_run(code=1, stderr="Tailscale is stopped.\n"), fake_run(stdout="not json"),
                    fake_run(raises=subprocess.TimeoutExpired("tailscale", 10)), fake_run(raises=OSError("boom")),
                    fake_run(stdout=json.dumps({"BackendState": "NeedsLogin", "Self": {}})),
                    fake_run(stdout=json.dumps({"Self": {"DNSName": "100.64.0.1."}}))):
            info = phone.tailscale_info(which=lambda n: "/x/tailscale", run=run)
            self.assertEqual((info["installed"], info["name"]), (True, None))
            self.assertTrue(info["note"])


class PhoneCommandTests(helpers.TempCase):
    def run_phone(self, info, **kw):
        lines = []
        cfg = kw.pop("cfg", None) or Config({}, self.paths, env={})
        code = phone.run_phone(cfg, out=lines.append, info=info, **kw)
        return code, "\n".join(lines)

    def known(self):
        return {"installed": True, "name": NAME, "state": "Running", "note": ""}

    def test_with_tailscale_it_prints_the_name_the_two_commands_and_the_warnings(self):
        code, out = self.run_phone(self.known())
        self.assertEqual(code, 0)
        self.assertIn(f"This machine's name is {NAME}", out)
        self.assertIn(f'allowed_hosts = ["{NAME}"]', out)
        self.assertIn("tailscale serve --bg 8765", out)
        self.assertIn(f"https://{NAME}/", out)
        self.assertIn("unhoard serve --no-open", out)
        self.assertIn("NEVER use `tailscale funnel`", out)
        self.assertIn("press Go", out)
        self.assertIn("ssh -L 8765:127.0.0.1:8765", out)
        self.assertIn("Omnigent", out)
        self.assertIn("docs/phone.md", out)
        self.assertNotIn("tailscale funnel 8765", out)  # the command is never offered

    def test_without_tailscale_it_explains_and_uses_the_placeholder(self):
        code, out = self.run_phone({"installed": False, "name": None, "state": None, "note": "not found"})
        self.assertEqual(code, 0)
        self.assertIn("Tailscale was not found", out)
        self.assertIn("https://tailscale.com/download", out)
        self.assertIn(f'allowed_hosts = ["{NAME}"]', out)  # the placeholder
        self.assertIn("placeholder", out)
        self.assertIn("tailscale serve --bg 8765", out)
        self.assertIn("NEVER", out)

    def test_it_uses_the_configured_port_and_host_override(self):
        cfg = Config({"server": {"port": 9123}}, self.paths, env={})
        _, out = self.run_phone(self.known(), cfg=cfg, host="Other.Example.TS.net")
        self.assertIn("tailscale serve --bg 9123", out)
        self.assertIn("other.example.ts.net", out)
        self.assertIn("ssh -L 9123:127.0.0.1:9123", out)
        _, out = self.run_phone(self.known(), cfg=cfg, port=7000)
        self.assertIn("tailscale serve --bg 7000", out)

    def test_it_says_when_the_name_is_already_allowed(self):
        cfg = Config({"server": {"allowed_hosts": [NAME]}}, self.paths, env={})
        _, out = self.run_phone(self.known(), cfg=cfg)
        self.assertIn("already in your config", out)
        self.assertNotIn("let this command write it", out)

    def test_nothing_is_written_without_yes(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text("[server]\nport = 8765\n")
        before = self.paths.config_file.read_text()
        self.run_phone(self.known())
        self.assertEqual(self.paths.config_file.read_text(), before)

    def test_yes_writes_allowed_hosts_keeping_the_rest_and_the_mode(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text('# my notes\n[x]\nuser_id = "42"\n[server]\nport = 8765\n')
        os.chmod(self.paths.config_file, 0o600)
        code, out = self.run_phone(self.known(), yes=True)
        self.assertEqual(code, 0)
        text = self.paths.config_file.read_text()
        data = tomllib.loads(text)
        self.assertEqual((data["server"]["allowed_hosts"], data["server"]["port"], data["x"]["user_id"]), ([NAME], 8765, "42"))
        self.assertIn("# my notes", text)
        self.assertEqual(stat.S_IMODE(self.paths.config_file.stat().st_mode), 0o600)
        self.assertIn("Wrote [server] allowed_hosts", out)
        self.assertEqual([p.name for p in self.paths.config_file.parent.iterdir()], ["config.toml"])  # no temp files

    def test_yes_adds_to_existing_entries_and_is_idempotent(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text('[server]\nallowed_hosts = ["first.example.ts.net"]\n')
        self.run_phone(self.known(), yes=True)
        self.run_phone(self.known(), yes=True)
        data = tomllib.loads(self.paths.config_file.read_text())
        self.assertEqual(data["server"]["allowed_hosts"], ["first.example.ts.net", NAME])

    def test_yes_without_a_known_name_or_a_config_file_writes_nothing(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text("[server]\n")
        code, out = self.run_phone({"installed": False, "name": None, "state": None, "note": "x"}, yes=True)
        self.assertEqual(code, 1)
        self.assertIn("--host NAME", out)
        self.assertEqual(self.paths.config_file.read_text(), "[server]\n")
        self.paths.config_file.unlink()
        code, out = self.run_phone(self.known(), yes=True)
        self.assertEqual(code, 1)
        self.assertIn("unhoard setup", out)
        self.assertFalse(self.paths.config_file.exists())

    def test_yes_reports_an_unwritable_or_unparsable_config(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text("[server\nbroken")
        code, out = self.run_phone(self.known(), yes=True)
        self.assertEqual(code, 1)
        self.assertIn("Not written", out)
        self.assertIn(f'allowed_hosts = ["{NAME}"]', out)

    def test_yes_refuses_to_write_over_an_allowed_hosts_that_is_not_a_list_of_valid_names(self):
        self.paths.config_file.parent.mkdir(parents=True)
        for text in ('[server]\nallowed_hosts = "mybox.tail1234.ts.net"\n',  # a string would be split into letters
                     '[server]\nallowed_hosts = [1, 2]\n',
                     '[server]\nallowed_hosts = [["a.example"]]\n',
                     '[server]\nallowed_hosts = ["ok.example", "*"]\n',
                     '[server]\nallowed_hosts = ["https://x.example"]\n',
                     '[server]\nallowed_hosts = ["10.0.0.5"]\n',
                     'server = 3\n'):
            self.paths.config_file.write_text(text)
            code, out = self.run_phone(self.known(), yes=True)
            self.assertEqual(code, 1, text)
            self.assertIn("Not written, and nothing was changed", out)
            self.assertEqual(self.paths.config_file.read_text(), text, text)  # byte for byte

    def test_the_refusal_names_the_bad_entry_and_the_rule(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text('[server]\nallowed_hosts = ["ok.example", "*.example"]\n')
        _code, out = self.run_phone(self.known(), yes=True)
        self.assertIn("bad entry, '*.example'", out)
        self.assertIn("Fix it by hand", out)

    def test_yes_still_works_when_the_existing_entry_is_the_same_name_in_another_case(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text(f'[server]\nallowed_hosts = ["{NAME.upper()}"]\n')
        code, _out = self.run_phone(self.known(), yes=True)
        self.assertEqual(code, 0)
        self.assertEqual(tomllib.loads(self.paths.config_file.read_text())["server"]["allowed_hosts"], [NAME.upper()])

    def test_a_bad_host_option_is_refused(self):
        for bad in ("*", "https://x.example", "10.0.0.5"):
            code, out = self.run_phone(self.known(), host=bad, yes=True)
            self.assertEqual(code, 2, bad)
            self.assertIn("--host", out)


class OutputHygieneTests(unittest.TestCase):
    def test_control_and_invisible_characters_from_tailscale_are_not_printed(self):
        bad = json.dumps({"BackendState": "Run\x1b[31mning\u202e\n", "Self": {"DNSName": NAME + "."}})
        info = phone.tailscale_info(which=lambda n: "/x/tailscale", run=fake_run(stdout=bad))
        self.assertEqual(info["state"], "Run[31mning")
        self.assertEqual(info["name"], NAME)
        failing = phone.tailscale_info(which=lambda n: "/x/tailscale",
                                       run=fake_run(code=1, stderr="\x1b]0;title\x07boom \u202e\n"))
        self.assertEqual(failing["note"], "tailscale status failed: ]0;titleboom ")
        broken = phone.tailscale_info(which=lambda n: "/x/tailscale", run=fake_run(raises=OSError("no\x1b[2Jpe")))
        self.assertNotIn("\x1b", broken["note"])
        shown = phone.render(Config({}, SimpleNamespace(), env={}), 8765, {**info, "name": None, "note": failing["note"]}, None)
        self.assertNotIn("\x1b", shown)

    def test_a_name_with_control_characters_is_not_a_name(self):
        bad = json.dumps({"BackendState": "Running", "Self": {"DNSName": "my\x1b[2Jbox.ts.net."}})
        info = phone.tailscale_info(which=lambda n: "/x/tailscale", run=fake_run(stdout=bad))
        self.assertIsNone(info["name"])

    def test_the_search_path_ignores_the_current_directory_and_relative_entries(self):
        seen = []
        with mock.patch.dict(os.environ, {"PATH": f".{os.pathsep}{os.pathsep}/usr/bin{os.pathsep}bin{os.pathsep}/opt/x"}), \
                mock.patch.object(phone.shutil, "which", side_effect=lambda n, path=None: seen.append(path) or "/usr/bin/tailscale"):
            self.assertEqual(phone.find_tailscale(), "/usr/bin/tailscale")
        self.assertEqual(seen, [f"/usr/bin{os.pathsep}/opt/x"])
        with mock.patch.dict(os.environ, {"PATH": f".{os.pathsep}"}), \
                mock.patch.object(phone.shutil, "which", side_effect=AssertionError("must not search")):
            self.assertIsNone(phone.find_tailscale())

    def test_a_tailscale_file_in_the_current_directory_is_not_found(self):
        import tempfile
        here = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            exe = os.path.join(tmp, "tailscale")
            with open(exe, "w") as handle:
                handle.write("#!/bin/sh\n")
            os.chmod(exe, 0o755)
            os.chdir(tmp)
            try:
                for path in (".", "", f"{os.pathsep}", f".{os.pathsep}/nonexistent"):
                    with mock.patch.dict(os.environ, {"PATH": path}):
                        self.assertIsNone(phone.find_tailscale(), path)
            finally:
                os.chdir(here)


class CliTests(helpers.TempCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        env = {"UNHOARD_HOME": str(self.paths.data_dir), "UNHOARD_CONFIG": str(self.paths.config_file)}
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_phone_subcommand_never_calls_the_real_tailscale(self):
        run = fake_run()
        with mock.patch.object(phone.shutil, "which", return_value="/usr/bin/tailscale"), \
                mock.patch.object(phone.subprocess, "run", run):
            code, out = self.run_cli("phone")
        self.assertEqual(code, 0)
        self.assertIn(NAME, out)
        self.assertEqual(run.calls, [["/usr/bin/tailscale", "status", "--json"]])
        with mock.patch.object(phone.shutil, "which", return_value=None):
            self.assertIn("Tailscale was not found", self.run_cli("phone")[1])

    def test_status_shows_allowed_hosts(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text(f'[server]\nallowed_hosts = ["{NAME}", "*"]\n')
        info = json.loads(self.run_cli("status")[1])
        self.assertEqual(info["allowed_hosts"], [NAME])  # the bad entry is ignored and reported
        self.assertTrue(any("allowed_hosts" in p for p in info["config_problems"]))
        self.paths.config_file.write_text("")
        self.assertEqual(json.loads(self.run_cli("status")[1])["allowed_hosts"], [])
        self.assertIs(json.loads(self.run_cli("status")[1])["require_tailscale_identity"], True)  # the default
        self.paths.config_file.write_text("[server]\nrequire_tailscale_identity = false\n")
        self.assertIs(json.loads(self.run_cli("status")[1])["require_tailscale_identity"], False)
        self.paths.config_file.write_text('[server]\nrequire_tailscale_identity = "no"\n')
        info = json.loads(self.run_cli("status")[1])
        self.assertTrue(any("require_tailscale_identity must be bool" in p for p in info["config_problems"]))
        self.assertIs(info["require_tailscale_identity"], True)  # a wrong type never turns the check off

    def test_doctor_row_appears_only_when_hosts_are_set(self):
        self.assertIsNone(doctor.check_phone(Config({}, self.paths, env={})))
        row = doctor.check_phone(Config({"server": {"allowed_hosts": [NAME]}}, self.paths, env={}))
        self.assertEqual((row.name, row.status), ("phone access", doctor.WARN))
        self.assertIn(NAME, row.detail)
        self.assertIn("never `tailscale funnel`", row.detail)
        self.assertIn("press Go", row.detail)
        self.assertNotIn("identity", row.detail)
        off = doctor.check_phone(Config({"server": {"allowed_hosts": [NAME], "require_tailscale_identity": False}},
                                        self.paths, env={}))
        self.assertIn("require_tailscale_identity is OFF", off.detail)
        bad = doctor.check_phone(Config({"server": {"allowed_hosts": ["*"]}}, self.paths, env={}))
        self.assertEqual(bad.status, doctor.WARN)
        self.assertIn("invalid", bad.detail)


if __name__ == "__main__":
    unittest.main()
