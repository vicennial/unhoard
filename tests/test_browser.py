"""Static checks of the card page script, and a real-browser check of the relate URLs when Playwright is around.

The browser part runs ``browser_check.py`` with a Python that has Playwright and Chromium: set
``UNHOARD_QA_PYTHON`` to its path. It is skipped when the variable is unset or not a file.
It serves the fictional demo cards on a loopback port with a launcher that does nothing.
"""
import helpers
import os
import subprocess
import threading
import unittest
from pathlib import Path
from unittest import mock

from unhoard import demo, launch
from unhoard.config import Config, Paths
from unhoard.server import BookmarksServer
from unhoard.store import connect

WEB = Path(helpers.ROOT) / "src" / "unhoard" / "web"
SCRIPT = Path(__file__).resolve().parent / "browser_check.py"


def qa_python():
    path = os.environ.get("UNHOARD_QA_PYTHON")
    return path if path and Path(path).is_file() else None


class PageScriptTests(unittest.TestCase):
    def setUp(self):
        self.js = (WEB / "app.js").read_text(encoding="utf-8")

    def test_the_page_script_shows_relate_urls_before_go(self):
        self.assertIn("relate_urls", self.js)
        self.assertIn("Links in this task", self.js)

    def test_links_are_https_only_and_noopener(self):
        # the link branch is guarded by https(), and every target=_blank link carries rel noopener noreferrer
        self.assertIn("marked === u ? https(u) : null", self.js)  # the link branch needs https and no hidden characters
        for line in self.js.splitlines():
            if 'target: "_blank"' in line:
                self.assertIn("noopener noreferrer", line, line)

    def test_hidden_character_class_names_the_tag_block_with_the_u_flag(self):
        line = next(l for l in self.js.splitlines() if l.lstrip().startswith("var HIDDEN = /"))
        self.assertTrue(line.rstrip().endswith("]/gu;"), line)
        # tag block, U+2028-202F (includes U+202E), zero-width and marks U+200B-200F, U+205F-206F (includes U+2066-2069), BOM
        for needle in (r"\u{E0000}-\u{E007F}", r"\u2028-\u202f", r"\u200b-\u200f", r"\u205f-\u206f", r"\ufeff"):
            self.assertIn(needle, line)
        self.assertIn("codePointAt(0)", self.js)  # astral characters get their full code point in the marker

    def test_urls_are_relative_so_the_page_works_under_a_path_prefix(self):
        # A reverse proxy may mount the page at /some/prefix/; absolute /api or /static URLs would escape it.
        html = (WEB / "index.html").read_text(encoding="utf-8")
        self.assertNotIn('"/static/', html)
        self.assertNotIn('api("/', self.js)
        self.assertNotIn('fetch("/', self.js)

    def test_the_checklist_is_text_content_and_storage_is_guarded(self):
        self.assertIn('api("api/setup")', self.js)
        self.assertIn("DISMISS_KEY", self.js)
        # every localStorage access sits inside a try block
        block = self.js[self.js.index("function dismissed"):self.js.index("function copyCommand")]
        self.assertIn("try {", block)
        self.assertIn("catch", block)
        self.assertEqual(self.js.count("localStorage"), block.count("localStorage"))
        self.assertIn("navigator.clipboard", self.js)
        self.assertIn('execCommand("copy")', self.js)  # the select-and-copy fallback

    def test_go_tells_the_server_which_target_it_showed_and_reloads_when_the_tab_returns(self):
        self.assertIn("expect_target", self.js)
        self.assertIn('"target_changed"', self.js)
        self.assertIn('addEventListener("visibilitychange"', self.js)
        # the post-Go notice is built from the server's answer, not from the label that was on the button
        notice = self.js[self.js.index("function goneNotice"):self.js.index("function showBrief")]
        self.assertIn("r.launch_target", notice)
        self.assertIn("pre.textContent = text", self.js)  # the brief is plain text

    def test_post_and_agent_text_never_goes_through_innerhtml(self):
        code = "\n".join(l for l in self.js.splitlines() if not l.strip().startswith("//"))
        for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write"):
            self.assertNotIn(forbidden, code)


@unittest.skipUnless(qa_python(), "set UNHOARD_QA_PYTHON to a Python with Playwright")
class RelateUrlsInBrowserTests(helpers.TempCase):
    def serve(self, paths, cfg):
        server = BookmarksServer(0, lambda: connect(paths), lambda card, note: {
            "ref": "printed:x", "status": "printed", "target": "print", "brief": "BRIEF <b>not html</b> TEXT"}, 1, 3, True, cfg)
        server.quiet = True
        thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def test_relate_urls_and_first_run_checklist_in_a_browser(self):
        demo.seed(self.db)
        self.db.commit()
        base = self.serve(self.paths, Config({}, self.paths, env={}))
        # a second data directory holds one real (non-demo id) enriched bookmark
        real_paths = Paths(self.tmp / "real-data", self.tmp / "real-config" / "config.toml")
        real_db = connect(real_paths)
        self.addCleanup(real_db.close)
        helpers.real_enriched(real_db)
        real = self.serve(real_paths, Config({}, real_paths, env={}))
        # a third server where Omnigent is found and an agent is chosen: its button says Go
        go_paths = Paths(self.tmp / "go-data", self.tmp / "go-config" / "config.toml")
        go_db = connect(go_paths)
        self.addCleanup(go_db.close)
        demo.seed(go_db)
        go_db.commit()
        with mock.patch.object(launch, "probe_omnigent", return_value=True):
            go = self.serve(go_paths, Config({"omnigent": {"agent_id": "ag_1"}}, go_paths, env={}))
            shots = self.tmp / "shots"
            shots.mkdir()
            done = self.check(base, shots, real, go)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)
        self.assertIn("OK", done.stdout)

    def check(self, base, shots, real, go):
        return subprocess.run([qa_python(), "-I", str(SCRIPT), base, str(shots), real, go],
                              capture_output=True, text=True, timeout=180)


if __name__ == "__main__":
    unittest.main()
