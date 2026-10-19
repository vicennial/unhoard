"""Live smoke tests against the real `claude` / `codex` on this machine. Skipped unless UNHOARD_LIVE=1.

They make a few tiny real agent calls (small token cost) and need the CLIs to be logged in. They use a temporary
data directory, a temporary memory folder and no MCP servers, and never touch your config.
"""
import helpers
import os
import shutil
import unittest

from unhoard import doctor, enrich, sync

LIVE = os.environ.get("UNHOARD_LIVE") == "1"
POST = {"data": [{"id": "9001", "text": "Test sharding for CI: split a slow suite across N workers by historical timing.",
                  "author_id": "7", "created_at": "2026-10-01T10:00:00.000Z"}],
        "includes": {"users": [{"id": "7", "username": "ada.example", "name": "Ada Example"}]}}


@unittest.skipUnless(LIVE, "set UNHOARD_LIVE=1 to run against the real agents")
class LiveTests(helpers.TempCase):
    def setUp(self):
        super().setUp()
        self.memory = self.tmp / "memory"
        self.memory.mkdir()
        (self.memory / "MEMORY.md").write_text("- Project zebra-ci: the reader is writing a Rust test runner that shards by timing.\n")
        sync.ingest_payload(self.db, POST)

    def cfg(self, agent):
        return self.config(enrich={"agent": agent}, context={"memory_folders": [str(self.memory)],
                                                              "index_files": [str(self.memory / "MEMORY.md")]})

    def enrich_once(self, agent):
        cfg = self.cfg(agent)
        out = enrich.enrich_pending(self.db, cfg, limit=1)
        self.assertEqual(out["errors"], [], out)
        row = self.db.execute("SELECT summary, why_saved FROM enrichments").fetchone()
        self.assertTrue(row["summary"] and row["why_saved"])
        return row

    @unittest.skipUnless(shutil.which("claude"), "claude is not installed")
    def test_claude_preset_end_to_end_and_doctor(self):
        cfg = self.cfg("claude")
        checks = doctor.run_doctor(cfg)
        self.assertNotIn("fail", {c.status for c in checks}, doctor.render(checks))
        row = self.enrich_once("claude")
        print("\nlive claude why_saved:", row["why_saved"])

    @unittest.skipUnless(shutil.which("codex"), "codex is not installed")
    def test_codex_preset_end_to_end_when_the_sandbox_works(self):
        sandbox = doctor.check_codex_sandbox(doctor.default_runner, 180, self.cfg("codex"))
        if sandbox.status != "pass":
            self.skipTest("the Codex sandbox cannot run commands here: " + sandbox.detail)
        row = self.enrich_once("codex")
        print("\nlive codex why_saved:", row["why_saved"])


if __name__ == "__main__":
    unittest.main()
