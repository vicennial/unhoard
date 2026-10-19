import helpers
import http.client
import json
import threading
import unittest
from unittest import mock

from unhoard import demo, firstrun, xauth
from unhoard import launch as launch_mod
from unhoard.config import Config
from unhoard.server import CSRF_HEADER, BookmarksServer
from unhoard.store import connect

TOKEN = "xtok-SECRET-VALUE-DO-NOT-PRINT"


class Served(helpers.TempCase):
    def serve(self, cfg):
        server = BookmarksServer(0, lambda: connect(self.paths), lambda card, note: {"ref": "x", "status": "started"},
                                 3, 3, True, cfg)
        server.quiet = True
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()

        def stop():
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)
        self.addCleanup(stop)
        self.port = server.server_port

    def call(self, method, path, headers=None, host=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        if host:
            conn.putrequest(method, path, skip_host=True)
            conn.putheader("Host", host)
            for k, v in (headers or {}).items():
                conn.putheader(k, v)
            conn.putheader("Content-Length", "0")
            conn.endheaders()
        else:
            conn.request(method, path, headers=headers or {})
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read()

    def setup_json(self):
        status, _, body = self.call("GET", "/api/setup")
        self.assertEqual(status, 200)
        return json.loads(body)

    @staticmethod
    def by_id(data):
        return {i["id"]: i for i in data["items"]}


class StatusTests(Served):
    def test_unconfigured(self):
        cfg = self.config()
        self.serve(cfg)
        data = self.setup_json()
        items = self.by_id(data)
        self.assertEqual({k: v["status"] for k, v in items.items()},
                         {"x_access": "todo", "user_id": "todo", "agent": "todo", "launch": "info", "data": "todo", "demo": "ok"})
        self.assertEqual(items["x_access"]["command"], "unhoard login x")
        self.assertIn("X_BEARER_TOKEN", items["x_access"]["detail"])
        self.assertEqual(items["user_id"]["command"], "unhoard setup")
        self.assertEqual(items["agent"]["command"], "unhoard setup")
        self.assertEqual(items["data"]["command"], "unhoard sync && unhoard enrich --limit 5")
        self.assertEqual([i["id"] for i in data["items"] if i["next"]], ["x_access"])
        self.assertTrue(data["first_run"])
        self.assertFalse(data["demo"])
        self.assertEqual((data["ready"], data["total"]), (2, 6))  # "info" is a note, not a to-do: it counts as ready

    def test_configured(self):
        cfg = Config({"x": {"user_id": "42"}, "enrich": {"agent": "claude"},
                      "launch": {"adapter": "omnigent"}, "omnigent": {"agent_id": "ag_1"}},
                     self.paths, env={"X_BEARER_TOKEN": TOKEN})
        helpers.real_enriched(self.db)
        self.serve(cfg)
        data = self.setup_json()
        self.assertEqual({i["status"] for i in data["items"]}, {"ok"})
        self.assertFalse(data["first_run"])
        self.assertEqual((data["real_synced"], data["real_enriched"]), (1, 1))
        self.assertEqual((data["ready"], data["total"]), (6, 6))
        self.assertFalse(any(i["next"] or i["command"] for i in data["items"]))
        self.assertIn("X_BEARER_TOKEN", self.by_id(data)["x_access"]["detail"])

    def test_no_secret_in_the_output(self):
        cfg = Config({"x": {"bearer_token": TOKEN + "-FILE"}, "enrich": {"agent": "codex"}}, self.paths,
                     env={"X_BEARER_TOKEN": TOKEN})
        self.serve(cfg)
        _, _, body = self.call("GET", "/api/setup")
        self.assertNotIn(b"SECRET", body)
        self.assertEqual(self.by_id(json.loads(body))["x_access"]["status"], "ok")

    def test_token_source_in_one_helper(self):
        self.assertIsNone(firstrun.x_token_source(self.config()))
        self.assertEqual(firstrun.x_token_source(Config({}, self.paths, env={"X_BEARER_TOKEN": "t"})), "env")
        self.assertEqual(firstrun.x_token_source(Config({"x": {"bearer_token": "t"}}, self.paths, env={})), "config")
        # the env wins when both are set, as in Config.x_token
        self.assertEqual(firstrun.x_token_source(Config({"x": {"bearer_token": "t"}}, self.paths, env={"X_BEARER_TOKEN": "e"})), "env")

    def test_a_saved_login_is_the_third_source_and_shows_user_and_expiry_but_no_token(self):
        xauth.save_login(self.paths, {"access_token": "AT-LOGIN-SECRET", "refresh_token": "RT-LOGIN-SECRET",
                                      "expires_at": 4_000_000_000, "username": "alice", "client_id": "CID"})
        cfg = Config({}, self.paths, env={})
        self.assertEqual(firstrun.x_token_source(cfg), "login")
        self.assertEqual(firstrun.x_token_source(Config({"x": {"bearer_token": "t"}}, self.paths, env={})), "config")
        self.assertEqual(firstrun.x_token_source(Config({}, self.paths, env={"X_BEARER_TOKEN": "e"})), "env")
        item = self.by_id(firstrun.setup_status(cfg, self.db))["x_access"]
        self.assertEqual((item["status"], item["command"]), ("ok", None))
        self.assertIn("Logged in as @alice", item["detail"])
        self.assertIn("2096-", item["detail"])
        self.assertNotIn("SECRET", json.dumps(item))
        self.assertEqual(self.by_id(firstrun.setup_status(cfg, self.db))["user_id"]["status"], "warn")
        self.serve(cfg)
        _, _, body = self.call("GET", "/api/setup")
        self.assertNotIn(b"SECRET", body)

    def test_an_expired_login_is_described_without_a_token(self):
        xauth.save_login(self.paths, {"access_token": "AT-LOGIN-SECRET", "expires_at": 1, "username": "alice"})
        item = self.by_id(firstrun.setup_status(Config({}, self.paths, env={}), self.db))["x_access"]
        self.assertIn("cannot refresh", item["detail"])
        self.assertNotIn("SECRET", item["detail"])

    def test_user_id_is_a_warning_when_a_token_can_look_it_up(self):
        item = self.by_id(firstrun.setup_status(Config({}, self.paths, env={"X_BEARER_TOKEN": "t"}), self.db))["user_id"]
        self.assertEqual(item["status"], "warn")
        self.assertEqual(item["command"], "unhoard setup")

    def test_agent_presets_and_custom_commands(self):
        def agent(**enrich):
            return self.by_id(firstrun.setup_status(Config({"enrich": enrich}, self.paths, env={}), self.db))["agent"]
        self.assertEqual(agent(agent="codex")["status"], "ok")
        self.assertEqual(agent(read_command=["a"], relate_command=["b"])["status"], "ok")
        self.assertEqual(agent(read_command=["a"])["status"], "todo")

    def test_launch_adapters(self):
        def launch(**sections):
            return self.by_id(firstrun.setup_status(Config(sections, self.paths, env={}), self.db))["launch"]
        item = launch()  # the default is auto, and no Omnigent runs here
        self.assertEqual((item["status"], item["command"]), ("info", None))
        self.assertIn("Show brief", item["detail"])
        self.assertIn("Omnigent", item["detail"])
        self.assertIn('adapter = "command"', item["detail"])
        item = launch(launch={"adapter": "print"})
        self.assertEqual(item["status"], "ok")
        self.assertIn("Show brief", item["detail"])
        item = launch(launch={"adapter": "omnigent"})
        self.assertEqual((item["status"], item["command"]), ("warn", "unhoard agents"))
        self.assertEqual(launch(launch={"adapter": "omnigent"}, omnigent={"agent_id": "a"})["status"], "ok")
        self.assertEqual(launch(launch={"adapter": "command"})["status"], "warn")
        self.assertEqual(launch(launch={"adapter": "command"}, command={"argv": ["cat"]})["status"], "ok")

    def test_launch_with_auto_describes_the_effective_target(self):
        def launch(found, **sections):
            with mock.patch.object(launch_mod, "probe_omnigent", return_value=found):
                return self.by_id(firstrun.setup_status(Config(sections, self.paths, env={}), self.db))["launch"]
        up_with_agent = launch(True, omnigent={"agent_id": "ag_1"})
        self.assertEqual((up_with_agent["status"], up_with_agent["command"]), ("ok", None))
        self.assertIn("Go starts a session there", up_with_agent["detail"])
        no_agent = launch(True)
        self.assertEqual((no_agent["status"], no_agent["command"]), ("warn", "unhoard agents"))
        self.assertIn("no agent is chosen", no_agent["detail"])
        down = launch(False, omnigent={"agent_id": "ag_1"})  # an agent is set but Omnigent is not there: worth a warning
        self.assertEqual((down["status"], down["command"]), ("warn", None))
        self.assertIn("does not answer", down["detail"])
        self.assertIn("Show brief", down["detail"])
        none = launch(False)  # no agent and no Omnigent: just a note
        self.assertEqual((none["status"], none["command"]), ("info", None))
        explicit = launch(True, launch={"adapter": "print"}, omnigent={"agent_id": "ag_1"})  # explicit wins
        self.assertIn("prints the task brief", explicit["detail"])

    def test_launch_command_never_carries_a_config_value(self):
        cfg = Config({"launch": {"adapter": "omnigent"}, "omnigent": {"api": "http://x; rm -rf ~ #"}}, self.paths, env={})
        self.assertEqual(self.by_id(firstrun.setup_status(cfg, self.db))["launch"]["command"], "unhoard agents")

    def test_data_steps(self):
        cfg = self.config()
        self.assertEqual(self.by_id(firstrun.setup_status(cfg, self.db))["data"]["status"], "todo")
        self.db.execute("""INSERT INTO bookmarks(tweet_id,text,first_seen_at,last_seen_at,source_url,raw_json)
                           VALUES('1700000000000000001','t','2026-10-01','2026-10-01','https://x.com/a.example/status/1','{}')""")
        self.db.commit()
        item = self.by_id(firstrun.setup_status(cfg, self.db))["data"]
        self.assertEqual((item["status"], item["command"]), ("todo", "unhoard enrich --limit 5"))
        self.assertIn("1 synced", item["detail"])


class DemoTests(Served):
    def test_demo_ids_start_with_the_prefix(self):
        self.assertTrue(demo.SAMPLES)
        for sample in demo.SAMPLES:
            self.assertTrue(sample[0].startswith(firstrun.DEMO_ID_PREFIX), sample[0])

    def test_demo_cards_are_not_real_cards(self):
        demo.seed(self.db)
        cfg = self.config()
        data = firstrun.setup_status(cfg, self.db)
        self.assertEqual((data["real_synced"], data["real_enriched"], data["demo_cards"]), (0, 0, len(demo.SAMPLES)))
        self.assertTrue(data["demo"])
        self.assertTrue(data["first_run"])
        item = self.by_id(data)["demo"]
        self.assertEqual(item["status"], "warn")
        self.assertIn("sample cards", item["detail"])
        self.assertEqual(item["command"], "unhoard login x")  # the next step of the checklist

    def test_one_real_enriched_bookmark_ends_the_first_run(self):
        demo.seed(self.db)
        helpers.real_enriched(self.db)
        data = firstrun.setup_status(self.config(), self.db)
        self.assertEqual((data["real_synced"], data["real_enriched"]), (1, 1))
        self.assertFalse(data["first_run"])
        self.assertFalse(data["demo"])
        self.assertEqual(self.by_id(data)["demo"]["status"], "ok")

    def test_a_real_bookmark_that_is_not_enriched_keeps_the_first_run(self):
        self.db.execute("""INSERT INTO bookmarks(tweet_id,text,first_seen_at,last_seen_at,source_url,raw_json)
                           VALUES('1700000000000000001','t','2026-10-01','2026-10-01','https://x.com/a.example/status/1','{}')""")
        self.db.commit()
        self.assertTrue(firstrun.setup_status(self.config(), self.db)["first_run"])


class GuardTests(Served):
    def test_host_and_origin_checks(self):
        self.serve(self.config())
        self.assertEqual(self.call("GET", "/api/setup")[0], 200)
        self.assertEqual(self.call("GET", "/api/setup", host="evil.example:80")[0], 403)
        self.assertEqual(self.call("GET", "/api/setup", headers={"Origin": "https://evil.example"})[0], 403)
        self.assertEqual(self.call("GET", "/api/setup", headers={"Origin": f"http://127.0.0.1:{self.port}"})[0], 200)

    def test_get_only(self):
        self.serve(self.config())
        self.assertEqual(self.call("POST", "/api/setup", headers={CSRF_HEADER: "1"})[0], 404)
        self.assertEqual(self.call("POST", "/api/setup")[0], 403)

    def test_headers_and_unchanged_csp(self):
        self.serve(self.config())
        _, headers, _ = self.call("GET", "/api/setup")
        self.assertEqual(headers["Content-Type"], "application/json; charset=utf-8")
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertIn("default-src 'none'", headers["Content-Security-Policy"])
        self.assertIn("connect-src 'self'", headers["Content-Security-Policy"])

    def test_without_a_config_there_is_no_endpoint(self):
        self.serve(None)
        self.assertEqual(self.call("GET", "/api/setup")[0], 404)


if __name__ == "__main__":
    unittest.main()
