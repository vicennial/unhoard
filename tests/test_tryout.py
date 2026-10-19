import helpers
import contextlib
import io
import json
import os
import signal
import tempfile
import threading
import unittest
import urllib.request
import webbrowser
from pathlib import Path
from unittest import mock

from unhoard import cli, server, tryout
from unhoard.server import BookmarksServer

REAL_SERVE_FOREVER = BookmarksServer.serve_forever


def fetch(url, data=None, headers=None):
    req = urllib.request.Request(url, data=data, headers=headers or {})
    with urllib.request.urlopen(req, timeout=10) as resp:
        return resp.status, resp.read()


class Env(helpers.TempCase):
    """Real config and data live under a folder that must stay untouched; the browser is never opened for real."""

    def setUp(self):
        super().setUp()
        self.real = self.tmp / "real"
        env = {"UNHOARD_HOME": str(self.real / "data"), "UNHOARD_CONFIG": str(self.real / "config.toml"),
               "HOME": str(self.tmp), "X_BEARER_TOKEN": "tok-should-not-be-used"}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.opened = []
        patcher = mock.patch.object(webbrowser, "open", side_effect=lambda url: self.opened.append(url) or True)
        patcher.start()
        self.addCleanup(patcher.stop)
        patcher = mock.patch.object(server, "can_open_browser", return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()


class TryTests(Env):
    def test_try_serves_the_demo_in_a_temp_dir_and_removes_it(self):
        seen = {}
        made = []
        real_mkdtemp = tempfile.mkdtemp

        def mkdtemp(*args, **kwargs):
            made.append(real_mkdtemp(*args, **kwargs))
            return made[-1]

        def serve_briefly(srv):
            thread = threading.Thread(target=lambda: REAL_SERVE_FOREVER(srv, poll_interval=0.02), daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{srv.server_port}"
            seen["exists_while_running"] = Path(made[0]).is_dir()
            seen["page"] = fetch(base + "/")[0]
            board = json.loads(fetch(base + "/api/cards")[1])
            seen["cards"] = [c["author_name"] for c in board["picks"] + board["waiting"]]
            seen["avatars"] = [c["author_avatar_url"][:23] for c in board["picks"]]
            card = board["picks"][0]
            go = fetch(f"{base}/api/cards/{card['action_id']}/decide", json.dumps({"decision": "go"}).encode(),
                       {"Content-Type": "application/json", "X-Unhoard": "1"})
            seen["go"] = json.loads(go[1])
            srv.shutdown()
            thread.join(5)
            raise KeyboardInterrupt

        with mock.patch.object(tempfile, "mkdtemp", mkdtemp), \
                mock.patch.object(BookmarksServer, "serve_forever", serve_briefly):
            code, out, err = self.run_cli("try")
        self.assertEqual((code, err), (0, ""))
        self.assertEqual(len(made), 1)
        self.assertTrue(seen["exists_while_running"])
        self.assertFalse(Path(made[0]).exists(), "the temporary folder must be removed on exit")
        self.assertEqual(seen["page"], 200)
        # four cards (Daniel has two); Tom's post has no task, so no card
        self.assertEqual(sorted(seen["cards"]), ["Daniel Reyes", "Daniel Reyes", "Maya Okafor", "Priya Nair"])
        self.assertTrue(all(a == "data:image/jpeg;base64," for a in seen["avatars"]))
        self.assertEqual((seen["go"]["status"], seen["go"]["launch_status"]), ("accepted", "printed"))  # print adapter
        self.assertEqual(len(self.opened), 1)
        self.assertRegex(self.opened[0], r"^http://127\.0\.0\.1:\d+/$")
        self.assertIn(self.opened[0], out)
        self.assertIn("Ctrl+C to stop", out)
        self.assertIn("We cut CI from 40 min to 6.", out)  # Go printed the brief in the terminal
        self.assertNotIn("tok-should-not-be-used", out)

    def test_try_never_touches_the_real_config_or_data(self):
        with mock.patch.object(BookmarksServer, "serve_forever", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_cli("try", "--no-open")[0], 0)
        self.assertFalse(self.real.exists())
        self.assertEqual(self.opened, [])

    def test_try_config_ignores_the_environment(self):
        root = self.tmp / "t"
        cfg = tryout.make_tryout_config(root)
        self.assertEqual(cfg.x_token, "")
        self.assertEqual(cfg.get("launch", "adapter"), "print")
        self.assertTrue(str(cfg.paths.db_file).startswith(str(root.resolve())))
        self.assertTrue(str(cfg.paths.config_file).startswith(str(root.resolve())))

    def test_the_folder_is_removed_when_the_server_cannot_start(self):
        made = []
        real_mkdtemp = tempfile.mkdtemp
        with mock.patch.object(tempfile, "mkdtemp", lambda *a, **k: made.append(real_mkdtemp(*a, **k)) or made[-1]), \
                mock.patch.object(server, "make_server", side_effect=OSError("address in use")):
            code, _, err = self.run_cli("try", "--port", "1")
        self.assertEqual(code, 1)
        self.assertIn("address in use", err)
        self.assertFalse(Path(made[0]).exists())

    def test_sigterm_is_turned_into_a_clean_stop(self):
        def serve_then_term(srv):
            os.kill(os.getpid(), signal.SIGTERM)

        made = []
        real_mkdtemp = tempfile.mkdtemp
        with mock.patch.object(tempfile, "mkdtemp", lambda *a, **k: made.append(real_mkdtemp(*a, **k)) or made[-1]), \
                mock.patch.object(BookmarksServer, "serve_forever", serve_then_term):
            self.assertEqual(self.run_cli("try", "--no-open")[0], 0)
        self.assertFalse(Path(made[0]).exists())
        self.assertIs(signal.getsignal(signal.SIGTERM), signal.SIG_DFL)  # the old handler is back


    def test_sighup_is_turned_into_a_clean_stop_too(self):
        def serve_then_hup(srv):
            os.kill(os.getpid(), signal.SIGHUP)

        made = []
        before = signal.getsignal(signal.SIGHUP)
        real_mkdtemp = tempfile.mkdtemp
        with mock.patch.object(tempfile, "mkdtemp", lambda *a, **k: made.append(real_mkdtemp(*a, **k)) or made[-1]), \
                mock.patch.object(BookmarksServer, "serve_forever", serve_then_hup):
            self.assertEqual(self.run_cli("try", "--no-open")[0], 0)
        self.assertFalse(Path(made[0]).exists())
        self.assertIs(signal.getsignal(signal.SIGHUP), before)  # and the old handler is back


class ServeOpenTests(Env):
    def serve(self, *argv):
        with mock.patch.object(BookmarksServer, "serve_forever", side_effect=KeyboardInterrupt):
            return self.run_cli("serve", "--port", "0", *argv)

    def test_serve_opens_the_browser_once_and_prints_the_url(self):
        code, out, _ = self.serve()
        self.assertEqual(code, 0)
        self.assertEqual(len(self.opened), 1)
        self.assertIn(self.opened[0], out)
        self.assertRegex(self.opened[0], r"^http://127\.0\.0\.1:\d+/$")

    def test_no_open_skips_the_browser_but_prints_the_url(self):
        code, out, _ = self.serve("--no-open")
        self.assertEqual((code, self.opened), (0, []))
        self.assertIn("http://127.0.0.1:", out)

    def test_headless_skips_quietly_and_a_failing_browser_does_not_crash(self):
        with mock.patch.object(server, "can_open_browser", return_value=False):
            code, out, _ = self.serve()
        self.assertEqual((code, self.opened), (0, []))
        self.assertIn("http://127.0.0.1:", out)
        self.assertIn("Open the address above", out)
        with mock.patch.object(webbrowser, "open", side_effect=webbrowser.Error("no browser")):
            code, out, _ = self.serve()
        self.assertEqual(code, 0)
        self.assertIn("Open the address above", out)

    def test_the_browser_is_opened_only_after_the_server_is_listening(self):
        states = []

        def opened(url):
            states.append(fetch_status(url))
            return True

        def fetch_status(url):
            # the socket is bound and listening, so a connection is accepted (served once serve_forever runs)
            import socket
            port = int(url.rsplit(":", 1)[1].strip("/"))
            with socket.create_connection(("127.0.0.1", port), timeout=2):
                return "listening"

        with mock.patch.object(webbrowser, "open", opened):
            self.serve()
        self.assertEqual(states, ["listening"])

    def test_can_open_browser_rules(self):
        real = REAL_CAN_OPEN
        self.assertTrue(real({}, "darwin"))
        self.assertTrue(real({}, "win32"))
        self.assertFalse(real({}, "linux"))
        self.assertFalse(real({"DISPLAY": ""}, "linux"))
        self.assertTrue(real({"DISPLAY": ":0"}, "linux"))
        self.assertTrue(real({"WAYLAND_DISPLAY": "wayland-0"}, "linux"))


REAL_CAN_OPEN = server.can_open_browser


if __name__ == "__main__":
    unittest.main()
