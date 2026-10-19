import helpers
import contextlib
import io
import json
import os
import tomllib
import unittest
from argparse import Namespace
from unittest import mock

from unhoard import cli, config, omnigent_detect as od, tomledit

AGENTS = {"object": "list", "data": [
    {"id": "ag_jcode", "object": "agent", "name": "jcode", "harness": "jcode"},
    {"id": "ag_claude", "object": "agent", "name": "claude", "harness": "claude-sdk"},
    {"object": "agent", "name": "no id, dropped"}]}
DEAD = "http://127.0.0.1:9"  # nothing listens here


def omnigent(handler, body):
    if handler.path == "/v1/agents":
        return 200, AGENTS
    return 404, {"detail": "not found"}


class FetchTests(unittest.TestCase):
    def test_lists_agents_by_name_and_drops_entries_without_an_id(self):
        with helpers.fake_http(omnigent) as (base, reqs):
            found = od.detect(env={}, configured=base)
            self.assertEqual(found.api, base)
            self.assertEqual([(a.id, a.name, a.harness) for a in found.agents],
                             [("ag_jcode", "jcode", "jcode"), ("ag_claude", "claude", "claude-sdk")])
            self.assertEqual([r["path"] for r in reqs], ["/v1/agents"])
            self.assertEqual(reqs[0]["method"], "GET")
        self.assertEqual(od.describe(found), "  1. jcode (jcode)\n  2. claude (claude-sdk)")

    def test_a_bare_list_and_odd_payloads(self):
        self.assertEqual([a.id for a in od.parse_agents([{"id": "a"}, {"agent_id": "b", "name": "B"}, 5, {}])], ["a", "b"])
        for junk in (None, "x", {"data": "x"}, {"data": [None]}, 3):
            self.assertEqual(od.parse_agents(junk), [])

    def test_not_running_http_error_and_not_json_give_none(self):
        self.assertIsNone(od.fetch_agents(DEAD, timeout=0.5))
        with helpers.fake_http(lambda h, b: (500, {})) as (base, _):
            self.assertIsNone(od.fetch_agents(base, timeout=2))

    def test_a_probe_follows_no_redirect_and_uses_no_proxy(self):
        with helpers.fake_http(lambda h, b: (200, AGENTS)) as (target, target_reqs):
            def bounce(handler, body):
                return 302, {}, {"Location": target + "/v1/agents"}
            with helpers.fake_http(bounce) as (base, reqs):
                self.assertIsNone(od.fetch_agents(base, timeout=2))  # the 302 is an error, not followed
            self.assertEqual([r["path"] for r in reqs], ["/v1/agents"])
            self.assertEqual(target_reqs, [])  # the redirect target never saw a request
        # an HTTP proxy in the environment is ignored for loopback: it would otherwise get the request
        with helpers.fake_http(lambda h, b: (200, AGENTS)) as (proxy, proxy_reqs), helpers.fake_http(omnigent) as (base, _):
            with mock.patch.dict(os.environ, {"http_proxy": proxy, "HTTP_PROXY": proxy}):
                os.environ.pop("no_proxy", None)
                os.environ.pop("NO_PROXY", None)
                self.assertEqual([a.id for a in od.fetch_agents(base, timeout=2)], ["ag_jcode", "ag_claude"])
            self.assertEqual(proxy_reqs, [])

    def test_a_listener_that_is_not_http_is_just_not_omnigent(self):
        with helpers.garbage_listener() as base:  # for example SSH answering on the port
            self.assertIsNone(od.fetch_agents(base, timeout=2))
            with mock.patch.object(od, "DEFAULT_API", DEAD):  # never the real default address
                self.assertFalse(od.detect(env={}, configured=base, timeout=2).running)

    def test_only_loopback_is_ever_probed(self):
        refuse = mock.Mock()
        refuse.open.side_effect = OSError("refused")
        with mock.patch.object(od, "DEFAULT_API", DEAD), mock.patch.object(od.netsafe, "loopback_opener", return_value=refuse):
            found = od.detect(env={"OMNIGENT_API": "http://203.0.113.9:6767"}, configured="https://omnigent.example.invalid")
        self.assertFalse(found.running)
        self.assertEqual(found.skipped, ["http://203.0.113.9:6767", "https://omnigent.example.invalid"])
        self.assertEqual(found.tried, [DEAD])
        self.assertEqual([c.args[0].full_url for c in refuse.open.call_args_list], [DEAD + "/v1/agents"])
        self.assertFalse(od.is_loopback("http://127.0.0.1.evil.example.invalid:1"))
        self.assertFalse(od.is_loopback("file:///etc/passwd"))
        self.assertTrue(od.is_loopback("http://localhost:6767"))

    def test_env_address_comes_first_then_config_then_default(self):
        self.assertEqual(od.candidate_apis("http://127.0.0.1:2/", {"OMNIGENT_API": "http://127.0.0.1:1"}),
                         ["http://127.0.0.1:1", "http://127.0.0.1:2", od.DEFAULT_API])
        self.assertEqual(od.candidate_apis(od.DEFAULT_API, {}), [od.DEFAULT_API])

    def test_set_strings_replaces_adds_and_keeps_the_rest(self):
        text = '[launch]\nadapter = "print"\n\n[omnigent]\napi = "http://127.0.0.1:6767"\n# agent_id is below\nagent_id = ""\nproject = "P"\n'
        out = tomledit.set_strings(text, "omnigent", {"agent_id": "ag1", "api": "http://127.0.0.1:1", "host_id": "h"})
        data = tomllib.loads(out)
        self.assertEqual(data["omnigent"], {"api": "http://127.0.0.1:1", "agent_id": "ag1", "project": "P", "host_id": "h"})
        self.assertEqual(data["launch"], {"adapter": "print"})
        self.assertIn("# agent_id is below", out)
        self.assertEqual(tomllib.loads(tomledit.set_strings("", "omnigent", {"agent_id": "x"})), {"omnigent": {"agent_id": "x"}})


class CliCase(helpers.TempCase):
    def setUp(self):
        super().setUp()
        home = self.tmp / "home"
        home.mkdir()
        env = {"UNHOARD_HOME": str(self.paths.data_dir), "UNHOARD_CONFIG": str(self.paths.config_file),
               "HOME": str(home)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("X_BEARER_TOKEN", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "OMNIGENT_API"):
            os.environ.pop(name, None)

    def setup_with(self, base, answers=None, **flags):
        """Run `setup` with the Omnigent default address at ``base``; ``answers`` None = no terminal."""
        out = io.StringIO()
        ask = None if answers is None else (lambda prompt: (out.write(prompt + "\n"), next(answers))[1])
        args = Namespace(force=True, yes=flags.get("yes", False), ask=ask, no_discover=True)
        with mock.patch.object(od, "DEFAULT_API", base), mock.patch("sys.stdin.isatty", return_value=False), \
                contextlib.redirect_stdout(out):
            code = cli.cmd_setup(args)
        return code, out.getvalue()

    def config(self):
        return tomllib.loads(self.paths.config_file.read_text())


class SetupDetectTests(CliCase):
    def test_choosing_omnigent_lists_the_agents_and_saves_the_pick(self):
        with helpers.fake_http(omnigent) as (base, _):
            code, out = self.setup_with(base, iter(["", "claude", "omnigent", "2"]))
        self.assertEqual(code, 0)
        self.assertIn("1. jcode", out)
        self.assertIn("2. claude (claude-sdk)", out)
        data = self.config()
        self.assertEqual((data["launch"]["adapter"], data["omnigent"]["agent_id"], data["omnigent"]["api"]),
                         ("omnigent", "ag_claude", base))
        self.assertEqual(data["omnigent"]["project"], "X Bookmarks")  # the rest of the template is intact

    def test_a_wrong_number_asks_again_and_blank_chooses_later(self):
        with helpers.fake_http(omnigent) as (base, _):
            _, out = self.setup_with(base, iter(["", "claude", "omnigent", "9", "abc", ""]))
        self.assertIn("Type a number from 1 to 2", out)
        data = self.config()
        self.assertEqual((data["launch"]["adapter"], data["omnigent"]["agent_id"]), ("omnigent", ""))
        self.assertIn("unhoard agents", out)

    def test_running_omnigent_with_the_print_adapter_asks_once_and_no_keeps_print(self):
        with helpers.fake_http(omnigent) as (base, _):
            _, out = self.setup_with(base, iter(["", "claude", "print", "n"]))
        self.assertIn("Start tasks there when you press Go?", out)
        data = self.config()
        self.assertEqual((data["launch"]["adapter"], data["omnigent"]["agent_id"]), ("print", ""))

    def test_running_omnigent_with_the_print_adapter_yes_switches_and_picks(self):
        with helpers.fake_http(omnigent) as (base, _):
            self.setup_with(base, iter(["", "claude", "print", "y", "1"]))
        data = self.config()
        self.assertEqual((data["launch"]["adapter"], data["omnigent"]["agent_id"]), ("auto", "ag_jcode"))  # print would ignore the pick

    def test_the_default_adapter_just_needs_the_pick_and_asks_no_yes_no_question(self):
        with helpers.fake_http(omnigent) as (base, _):
            _, out = self.setup_with(base, iter(["", "claude", "", "2"]))  # blank = the default adapter
        self.assertNotIn("Start tasks there when you press Go?", out)
        data = self.config()
        self.assertEqual((data["launch"]["adapter"], data["omnigent"]["agent_id"], data["omnigent"]["api"]),
                         ("auto", "ag_claude", base))

    def test_yes_picks_nothing_and_prints_the_list_and_the_config_lines(self):
        with helpers.fake_http(omnigent) as (base, _):
            code, out = self.setup_with(base, None, yes=True)
        self.assertEqual(code, 0)
        self.assertIn("Nothing was chosen", out)
        self.assertIn('agent_id = "ag_jcode"', out)
        self.assertNotIn("adapter =", out.split("Wrote")[0])  # the default adapter needs only the [omnigent] lines
        data = self.config()
        self.assertEqual((data["launch"]["adapter"], data["omnigent"]["agent_id"], data["omnigent"]["api"]),
                         ("auto", "", "http://127.0.0.1:6767"))

    def test_not_running_keeps_the_current_behaviour(self):
        code, out = self.setup_with(DEAD, iter(["", "claude", "omnigent"]))
        self.assertEqual(code, 0)
        self.assertIn("Omnigent is not running", out)
        self.assertIn("unhoard agents", out)
        data = self.config()
        self.assertEqual((data["launch"]["adapter"], data["omnigent"]["agent_id"]), ("omnigent", ""))
        _, quiet = self.setup_with(DEAD, None, yes=True)  # print adapter: no nagging
        self.assertNotIn("Omnigent", quiet.split("Next:")[0].replace("Wrote", ""))

    def test_status_page_and_setup_do_not_crash_on_a_listener_that_is_not_http(self):
        import http.client
        import threading
        from unhoard import launch
        from unhoard.server import BookmarksServer
        from unhoard.store import connect
        with helpers.garbage_listener() as base, mock.patch.object(launch, "probe_omnigent", helpers.REAL_PROBE):
            self.paths.config_file.parent.mkdir(parents=True, exist_ok=True)
            self.paths.config_file.write_text(f'[omnigent]\napi = "{base}"\nagent_id = "ag_1"\n')
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(cli.main(["status"]), 0)
            self.assertEqual(json.loads(out.getvalue())["launch_target"], "print")
            cfg = config.Config.load(self.paths)
            server = BookmarksServer(0, lambda: connect(self.paths), launch.make_launcher(cfg), 3, 3, True, cfg)
            server.quiet = True
            thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
            thread.start()
            self.addCleanup(thread.join, 5)
            self.addCleanup(server.server_close)
            self.addCleanup(server.shutdown)
            for path in ("/api/cards", "/api/setup"):
                conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
                conn.request("GET", path)
                self.assertEqual(conn.getresponse().status, 200, path)
                conn.close()
            self.assertEqual(self.setup_with(base, None, yes=True)[0], 0)  # setup probes the default address too

    def test_the_command_adapter_is_not_probed(self):
        with mock.patch.object(od, "detect") as detect:
            self.assertEqual(od.setup_step("command", True, input), ("command", {}))
        detect.assert_not_called()


class AgentsCommandTests(CliCase):
    def run_agents(self, base, *argv, answers=None):
        out, err = io.StringIO(), io.StringIO()
        ask = None if answers is None else (lambda prompt: next(answers))
        # the default [omnigent] api is also the real default address: point both at ``base``
        with mock.patch.object(od, "DEFAULT_API", base), mock.patch.dict(config.DEFAULTS["omnigent"], {"api": base}), \
                contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            args = cli.build_parser().parse_args(["agents", *argv])
            args.ask = ask
            code = args.func(args, cli.Config.load())
        return code, out.getvalue(), err.getvalue()

    def test_picking_saves_agent_api_and_adapter_without_touching_the_rest(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text('# my notes\n[x]\nuser_id = "42"\n[launch]\nadapter = "print"\n[omnigent]\nproject = "Mine"\n')
        with helpers.fake_http(omnigent) as (base, _):
            code, out, _ = self.run_agents(base, answers=iter(["1"]))
        self.assertEqual(code, 0)
        data = self.config()
        self.assertEqual(data["omnigent"], {"project": "Mine", "api": base, "agent_id": "ag_jcode"})
        self.assertEqual((data["x"]["user_id"], data["launch"]["adapter"]), ("42", "auto"))  # print would ignore the pick
        self.assertIn('was "print"; it is now "auto"', out)
        self.assertIn("# my notes", self.paths.config_file.read_text())

    def pick_with_adapter(self, adapter):
        self.paths.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.config_file.write_text(f'[launch]\n{adapter}[omnigent]\nproject = "Mine"\n')
        with helpers.fake_http(omnigent) as (base, _):
            code, out, _ = self.run_agents(base, answers=iter(["2"]))
        self.assertEqual(code, 0)
        return self.config(), out

    def test_picking_leaves_the_default_and_the_other_explicit_adapters_alone(self):
        data, out = self.pick_with_adapter("")  # no adapter line at all: the default
        self.assertEqual((data["omnigent"]["agent_id"], "adapter" in data["launch"]), ("ag_claude", False))
        self.assertIn("Go starts it in Omnigent while Omnigent is running", out)
        data, _ = self.pick_with_adapter('adapter = "auto"\n')
        self.assertEqual(data["launch"]["adapter"], "auto")
        data, _ = self.pick_with_adapter('adapter = "omnigent"\n')
        self.assertEqual((data["launch"]["adapter"], data["omnigent"]["agent_id"]), ("omnigent", "ag_claude"))
        data, out = self.pick_with_adapter('adapter = "command"\n')  # an explicit adapter wins
        self.assertEqual((data["launch"]["adapter"], data["omnigent"]["agent_id"]), ("command", "ag_claude"))
        self.assertIn("is kept", out)

    def test_yes_only_lists(self):
        with helpers.fake_http(omnigent) as (base, _):
            code, out, _ = self.run_agents(base, "--yes")
        self.assertEqual(code, 0)
        self.assertIn('agent_id = "ag_claude"', out)
        self.assertFalse(self.paths.config_file.exists())

    def test_not_running_says_so_and_fails(self):
        code, _, err = self.run_agents(DEAD, "--yes")
        self.assertEqual(code, 1)
        self.assertIn("Omnigent is not running", err)

    def test_without_a_config_it_prints_the_lines_instead_of_writing(self):
        with helpers.fake_http(omnigent) as (base, _):
            code, out, _ = self.run_agents(base, answers=iter(["2"]))
        self.assertEqual(code, 1)
        self.assertIn('agent_id = "ag_claude"', out)
        self.assertFalse(self.paths.config_file.exists())


class LauncherPointerTests(unittest.TestCase):
    def test_missing_agent_id_points_at_the_agents_command(self):
        from unhoard.launch import LaunchError
        from unhoard.launch.omnigent import OmnigentLauncher
        with self.assertRaises(LaunchError) as ctx:
            OmnigentLauncher({"api": DEAD})({"title": "t", "action_id": "BMK-00000001"}, "")
        self.assertIn("unhoard agents", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
