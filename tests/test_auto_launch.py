"""The default launch adapter, ``auto``: Omnigent when it runs and an agent is chosen, else show the brief."""
import helpers
import contextlib
import http.client
import io
import json
import threading
import unittest
from unittest import mock

from unhoard import cli, doctor, launch, tryout
from unhoard.config import Config
from unhoard.server import BookmarksServer
from unhoard.store import connect

CARD = {"action_id": "BMK-0000000B", "title": "Draft a caching plan", "brief": "Write a one-page plan.",
        "label": "work", "priority": "high", "why_saved": "w", "source_url": "https://example.com/demo/1",
        "text": "Dependency caching cut our CI time."}
AGENTS = {"data": [{"id": "ag_1", "name": "one"}]}


def omnigent(handler, body):
    if handler.path == "/v1/agents":
        return 200, AGENTS
    if handler.path == "/v1/projects":
        return 200, {"data": [{"id": "proj_1", "name": "X Bookmarks"}]}
    if handler.path == "/v1/hosts":
        return 200, {"hosts": [{"host_id": "host_1", "name": "this-box", "status": "online"}]}
    if handler.path == "/v1/sessions":
        return 200, {"session_id": "sess_1"}
    if handler.path == "/v1/sessions/sess_1/events":
        return 200, {"queued": True}
    return 404, {}


class ResolveTests(helpers.TempCase):
    def resolve(self, up, **sections):
        with mock.patch.object(launch, "probe_omnigent", return_value=up):
            return launch.resolve_target(Config(sections, self.paths, env={}))

    def test_auto_is_the_default(self):
        self.assertEqual(Config({}, self.paths, env={}).get("launch", "adapter"), "auto")
        self.assertEqual(Config({}, self.paths, env={}).problems, [])

    def test_auto_uses_omnigent_only_when_it_answers_and_an_agent_is_chosen(self):
        t = self.resolve(True, omnigent={"agent_id": "ag_1"})
        self.assertEqual((t.configured, t.effective, t.go_label), ("auto", "omnigent", "Go"))
        t = self.resolve(True)  # running, but nobody picked an agent
        self.assertEqual((t.effective, t.go_label, t.omnigent_found, t.agent_set), ("print", "Show brief", True, False))
        self.assertIn("unhoard agents", t.reason)
        t = self.resolve(False, omnigent={"agent_id": "ag_1"})  # an agent, but no server
        self.assertEqual((t.effective, t.go_label, t.omnigent_found, t.agent_set), ("print", "Show brief", False, True))
        t = self.resolve(False)
        self.assertEqual((t.effective, t.go_label), ("print", "Show brief"))

    def test_a_blank_agent_id_counts_as_none(self):
        self.assertEqual(self.resolve(True, omnigent={"agent_id": "  "}).effective, "print")

    def test_an_explicit_adapter_always_wins_and_is_not_probed(self):
        for adapter, label in (("print", "Show brief"), ("command", "Go"), ("omnigent", "Go")):
            with mock.patch.object(launch, "probe_omnigent", side_effect=AssertionError("probed")):
                t = launch.resolve_target(Config({"launch": {"adapter": adapter}, "omnigent": {"agent_id": "a"}},
                                                 self.paths, env={}))
            self.assertEqual((t.configured, t.effective, t.go_label), (adapter, adapter, label))

    def test_the_adapter_values_are_validated(self):
        self.assertEqual(Config({"launch": {"adapter": "auto"}}, self.paths, env={}).problems, [])
        problems = Config({"launch": {"adapter": "nope"}}, self.paths, env={}).problems
        self.assertEqual(problems, ["[launch] adapter must be auto, print, command or omnigent"])


class ProbeTests(unittest.TestCase):
    def test_the_real_probe_sees_a_server_that_lists_agents_and_nothing_else(self):
        with helpers.fake_http(omnigent) as (base, reqs):
            self.assertTrue(helpers.REAL_PROBE(base))
            self.assertEqual([r["path"] for r in reqs], ["/v1/agents"])  # one GET, nothing created
        with helpers.fake_http(lambda h, b: (500, {})) as (base, _):
            self.assertFalse(helpers.REAL_PROBE(base))
        self.assertFalse(helpers.REAL_PROBE("http://127.0.0.1:9"))

    def test_a_non_loopback_address_is_never_probed(self):
        with mock.patch("unhoard.omnigent_detect.fetch_agents", side_effect=AssertionError("probed")):
            self.assertFalse(helpers.REAL_PROBE("https://omnigent.example.invalid"))
            self.assertFalse(helpers.REAL_PROBE("http://203.0.113.9:6767"))


class AutoLauncherTests(helpers.TempCase):
    def cfg(self, base, agent="ag_1", **launch_section):
        return Config({"launch": launch_section, "omnigent": {"api": base, "agent_id": agent}}, self.paths, env={})

    def test_go_starts_a_session_when_omnigent_answers_and_shows_the_brief_when_it_does_not(self):
        with mock.patch.object(launch, "probe_omnigent", helpers.REAL_PROBE):
            with helpers.fake_http(omnigent) as (base, reqs):
                out = launch.make_launcher(self.cfg(base))(CARD, "a note")
            self.assertEqual((out["ref"], out["status"], out["target"]), ("sess_1", "started", "omnigent"))
            self.assertEqual([r["path"] for r in reqs if r["method"] == "POST"],
                             ["/v1/sessions", "/v1/sessions/sess_1/events"])
            # the same Config, but the server is gone now: with an agent chosen and no word from the page, a
            # silent print would consume the card, so it is an error and the card goes back to the queue
            with self.assertRaisesRegex(launch.LaunchError, "does not answer"):
                launch.make_launcher(self.cfg("http://127.0.0.1:9"))(CARD, "")
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):  # but the page that showed "Show brief" may have it printed
                out = launch.make_launcher(self.cfg("http://127.0.0.1:9"))(CARD, "", expect="print")
            self.assertEqual((out["status"], out["target"]), ("printed", "print"))
            self.assertIn("Draft a caching plan", out["brief"])
            self.assertIn("Draft a caching plan", printed.getvalue())

    def test_it_is_decided_again_on_every_go(self):
        cfg = self.cfg("http://127.0.0.1:9", agent="")  # no agent: print is a normal answer
        launcher = launch.make_launcher(cfg)
        seen = []
        with mock.patch.object(launch, "probe_omnigent", side_effect=[False, True, False]), \
                mock.patch.object(launch, "_build", side_effect=lambda name, c: (lambda card, note: seen.append(name) or {})):
            for _ in range(3):
                launcher(CARD, "")
        self.assertEqual(seen, ["print", "print", "print"])  # still no agent
        cfg = self.cfg("http://127.0.0.1:9")
        launcher = launch.make_launcher(cfg)
        seen.clear()
        with mock.patch.object(launch, "probe_omnigent", side_effect=[True, False, True]), \
                mock.patch.object(launch, "_build", side_effect=lambda name, c: (lambda card, note: seen.append(name) or {})):
            launcher(CARD, "")
            with self.assertRaises(launch.LaunchError):
                launcher(CARD, "")
            launcher(CARD, "")
        self.assertEqual(seen, ["omnigent", "omnigent"])

    def test_expected_target_that_no_longer_holds_starts_nothing(self):
        launcher = launch.make_launcher(self.cfg("http://127.0.0.1:9"))
        built = []
        with mock.patch.object(launch, "_build", side_effect=lambda name, c: built.append(name)):
            with mock.patch.object(launch, "probe_omnigent", return_value=True):  # page showed Show brief, now up
                with self.assertRaises(launch.TargetChanged) as ctx:
                    launcher(CARD, "", expect="print")
            self.assertEqual((ctx.exception.shown, ctx.exception.now), ("print", "omnigent"))
            with mock.patch.object(launch, "probe_omnigent", return_value=False):  # page showed Go, now down
                with self.assertRaises(launch.TargetChanged) as ctx:
                    launcher(CARD, "", expect="omnigent")
            self.assertEqual((ctx.exception.shown, ctx.exception.now), ("omnigent", "print"))
        self.assertEqual(built, [])  # no adapter was even built

    def test_an_empty_adapter_means_auto_in_both_places(self):
        cfg = Config({"launch": {"adapter": ""}, "omnigent": {"api": "http://127.0.0.1:9", "agent_id": "ag_1"}},
                     self.paths, env={})
        self.assertEqual(cfg.problems, [])
        with mock.patch.object(launch, "probe_omnigent", return_value=True):
            self.assertEqual(launch.resolve_target(cfg).effective, "omnigent")
            self.assertIsInstance(launch.make_launcher(cfg), launch.AutoLauncher)
        cfg = Config({"launch": {"adapter": ""}}, self.paths, env={})
        with mock.patch.object(launch, "probe_omnigent", return_value=False):
            self.assertEqual(launch.resolve_target(cfg).effective, "print")

    def test_an_explicit_adapter_is_not_replaced_when_omnigent_is_up(self):
        with mock.patch.object(launch, "probe_omnigent", return_value=True):
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                out = launch.make_launcher(self.cfg("http://127.0.0.1:9", adapter="print"))(CARD, "")
        self.assertEqual(out["status"], "printed")

    def test_the_try_command_always_prints_even_when_omnigent_runs(self):
        cfg = tryout.make_tryout_config(self.tmp / "t")
        with mock.patch.object(launch, "probe_omnigent", side_effect=AssertionError("probed")):
            t = launch.resolve_target(cfg)
        self.assertEqual((t.configured, t.effective, t.go_label), ("print", "print", "Show brief"))


class PageApiTests(helpers.TempCase):
    def setUp(self):
        super().setUp()
        helpers.seed_basic(self.db)

    def serve(self, cfg, real_launcher=False):
        launcher = launch.make_launcher(cfg) if real_launcher else (lambda c, n: {"ref": "x", "status": "started"})
        server = BookmarksServer(0, lambda: connect(self.paths), launcher, 3, 3, True, cfg)
        server.quiet = True
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.addCleanup(stop)
        return server.server_port

    def cards(self, port):
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        self.addCleanup(conn.close)
        conn.request("GET", "/api/cards")
        return json.loads(conn.getresponse().read())

    def go(self, port, expect=None, card="BMK-0000000B"):
        body = {"decision": "go"}
        if expect:
            body["expect_target"] = expect
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        self.addCleanup(conn.close)
        conn.request("POST", f"/api/cards/{card}/decide", body=json.dumps(body),
                     headers={"Content-Type": "application/json", "X-Unhoard": "1"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())

    def status_of(self, card="BMK-0000000B"):
        return self.db.execute("SELECT status, launch_status FROM actions WHERE action_id=?", (card,)).fetchone()

    def test_down_at_load_then_up_at_go_is_refused_not_started(self):
        with helpers.fake_http(omnigent) as (base, reqs):
            cfg = Config({"omnigent": {"api": base, "agent_id": "ag_1"}}, self.paths, env={})
            with mock.patch.object(launch, "probe_omnigent", helpers.REAL_PROBE):
                port = self.serve(cfg, real_launcher=True)
                with mock.patch.object(launch, "probe_omnigent", return_value=False):
                    self.assertEqual(self.cards(port)["launch"]["go_label"], "Show brief")  # the page loaded like this
                code, body = self.go(port, expect="print")  # Omnigent has come up since
            self.assertEqual(code, 409)
            self.assertEqual(body["reason"], "target_changed")
            self.assertIn("reload", body["message"])
            self.assertEqual(body["launch"]["target"], "omnigent")
            self.assertEqual([r["path"] for r in reqs if r["method"] == "POST"], [])  # no session was started
        self.assertEqual(tuple(self.status_of()), ("proposed", None))  # and the card was not consumed

    def test_up_at_load_then_down_at_go_is_refused_not_printed(self):
        cfg = Config({"omnigent": {"api": "http://127.0.0.1:9", "agent_id": "ag_1"}}, self.paths, env={})
        port = self.serve(cfg, real_launcher=True)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed), mock.patch.object(launch, "probe_omnigent", return_value=False):
            code, body = self.go(port, expect="omnigent")  # the page showed "Go"
        self.assertEqual((code, body["reason"], body["launch"]["target"]), (409, "target_changed", "print"))
        self.assertEqual(printed.getvalue(), "")  # nothing was printed behind the user's back
        self.assertEqual(tuple(self.status_of()), ("proposed", None))

    def test_the_race_after_the_early_check_is_caught_by_the_launcher(self):
        cfg = Config({"omnigent": {"api": "http://127.0.0.1:9", "agent_id": "ag_1"}}, self.paths, env={})
        port = self.serve(cfg, real_launcher=True)
        # the server's early check sees Omnigent up; by the time the launcher looks again it is gone
        with mock.patch.object(launch, "probe_omnigent", side_effect=[True, False, False]):
            code, body = self.go(port, expect="omnigent")
        self.assertEqual((code, body["reason"]), (409, "target_changed"))
        self.assertEqual(tuple(self.status_of()), ("proposed", None))  # rolled back, no "launch failed" trace

    def test_without_a_target_from_the_page_a_chosen_agent_and_a_dead_omnigent_fails_and_rolls_back(self):
        cfg = Config({"omnigent": {"api": "http://127.0.0.1:9", "agent_id": "ag_1"}}, self.paths, env={})
        port = self.serve(cfg, real_launcher=True)
        code, body = self.go(port)
        self.assertEqual(code, 502)
        self.assertIn("does not answer", body["message"])
        self.assertEqual(tuple(self.status_of()), ("proposed", "launch_failed"))

    def test_show_brief_returns_the_brief_and_still_prints_it(self):
        port = self.serve(Config({}, self.paths, env={}), real_launcher=True)
        printed = io.StringIO()
        with contextlib.redirect_stdout(printed):
            code, body = self.go(port, expect="print")
        self.assertEqual(code, 200)
        self.assertEqual((body["launch_target"], body["launch_status"]), ("print", "printed"))
        self.assertIn("High task", body["brief"])  # the card's title, inside the brief
        self.assertIn("untrusted", body["brief"])
        self.assertIn("High task", printed.getvalue())
        self.assertEqual(self.status_of()["status"], "accepted")

    def test_an_omnigent_go_reports_the_target_and_no_brief(self):
        with helpers.fake_http(omnigent) as (base, reqs):
            cfg = Config({"omnigent": {"api": base, "agent_id": "ag_1"}}, self.paths, env={})
            with mock.patch.object(launch, "probe_omnigent", helpers.REAL_PROBE):
                port = self.serve(cfg, real_launcher=True)
                code, body = self.go(port, expect="omnigent")
        self.assertEqual((code, body["launch_target"], body["launch_ref"]), (200, "omnigent", "sess_1"))
        self.assertNotIn("brief", body)

    def test_a_bad_expected_target_is_a_400(self):
        port = self.serve(Config({}, self.paths, env={}), real_launcher=True)
        self.assertEqual(self.go(port, expect="x")[0], 400)

    def test_cards_say_what_go_will_do_so_the_button_label_is_right(self):
        cases = [({}, False, ("auto", "print", "Show brief")),
                 ({"omnigent": {"agent_id": "ag_1"}}, True, ("auto", "omnigent", "Go")),
                 ({"omnigent": {"agent_id": "ag_1"}}, False, ("auto", "print", "Show brief")),
                 ({"launch": {"adapter": "print"}}, True, ("print", "print", "Show brief")),
                 ({"launch": {"adapter": "command"}}, False, ("command", "command", "Go")),
                 ({"launch": {"adapter": "omnigent"}}, False, ("omnigent", "omnigent", "Go"))]
        for sections, up, expected in cases:
            with mock.patch.object(launch, "probe_omnigent", return_value=up):
                data = self.cards(self.serve(Config(sections, self.paths, env={})))
            info = data["launch"]
            self.assertEqual((info["configured"], info["target"], info["go_label"]), expected, sections)
            self.assertEqual(set(info), {"configured", "target", "go_label"})

    def test_page_script_labels_the_button_from_the_api(self):
        js = (helpers.ROOT / "src" / "unhoard" / "web" / "app.js").read_text()
        self.assertIn("go_label", js)
        self.assertIn('"Show brief"', js)


class StatusAndDoctorTests(helpers.TempCase):
    def run_cli(self, *argv):
        out = io.StringIO()
        env = {"UNHOARD_HOME": str(self.paths.data_dir), "UNHOARD_CONFIG": str(self.paths.config_file)}
        with mock.patch.dict("os.environ", env), contextlib.redirect_stdout(out):
            code = cli.main(list(argv))
        return code, out.getvalue()

    def test_status_shows_the_configured_and_the_effective_adapter(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text('[omnigent]\nagent_id = "ag_1"\n')
        for up, target in ((True, "omnigent"), (False, "print")):
            with mock.patch.object(launch, "probe_omnigent", return_value=up):
                info = json.loads(self.run_cli("status")[1])
            self.assertEqual((info["launch_adapter"], info["launch_target"]), ("auto", target))
            self.assertTrue(info["launch_target_reason"])

    def test_doctor_row_for_the_launch_target(self):
        def row(up, **sections):
            with mock.patch.object(launch, "probe_omnigent", return_value=up):
                return doctor.check_launch(Config(sections, self.paths, env={}))
        found = row(True, omnigent={"agent_id": "ag_1"})
        self.assertEqual((found.status, found.name), (doctor.PASS, "launch target"))
        self.assertIn("auto -> omnigent", found.detail)
        self.assertIn("auto -> print", row(False).detail)
        self.assertEqual(row(False).status, doctor.PASS)  # no agent chosen and no Omnigent: nothing is wrong
        chosen_but_down = row(False, omnigent={"agent_id": "ag_1"})
        self.assertEqual(chosen_but_down.status, doctor.WARN)  # an agent is chosen but Omnigent does not answer
        self.assertIn("does not answer", chosen_but_down.detail)
        no_agent = row(True)  # Omnigent is there but nothing is chosen: worth a warning
        self.assertEqual(no_agent.status, doctor.WARN)
        self.assertIn("agents", no_agent.detail)
        self.assertEqual(row(False, launch={"adapter": "omnigent"}).status, doctor.WARN)
        self.assertEqual(row(False, launch={"adapter": "print"}).status, doctor.PASS)

    def test_doctor_command_lists_the_launch_target_first(self):
        with mock.patch.object(doctor, "run_doctor", return_value=[]):
            code, out = self.run_cli("doctor")
        self.assertIn("launch target", out)
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
