import helpers
import contextlib
import io
import json
import os
import subprocess
import sys
import tomllib
import unittest
from argparse import Namespace
from pathlib import Path
from unittest import mock

from unhoard import cli, omnigent_detect

AGENT = str(Path(__file__).resolve().parent / "fake_agent.py")


class CliCase(helpers.TempCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"  # an empty home: discovery and MCP lookups never see the real one
        self.home.mkdir()
        env = {"UNHOARD_HOME": str(self.paths.data_dir), "UNHOARD_CONFIG": str(self.paths.config_file),
               "HOME": str(self.home)}
        patcher = mock.patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        for name in ("X_BEARER_TOKEN", "CLAUDE_CONFIG_DIR", "CODEX_HOME", "OMNIGENT_API"):
            os.environ.pop(name, None)
        # setup looks for a running Omnigent on the default address: point it at a port nobody listens on, so a real
        # server on this machine never changes what these tests see
        patcher = mock.patch.object(omnigent_detect, "DEFAULT_API", "http://127.0.0.1:9")
        patcher.start()
        self.addCleanup(patcher.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def write_config(self, text):
        self.paths.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.config_file.write_text(text)


class SetupTests(CliCase):
    def test_setup_yes_writes_valid_config(self):
        code, out, _ = self.run_cli("setup", "--yes")
        self.assertEqual(code, 0)
        data = tomllib.loads(self.paths.config_file.read_text())
        self.assertEqual(data["enrich"]["agent"], "claude")
        self.assertEqual((data["enrich"]["read_command"], data["enrich"]["relate_command"]), ([], []))  # the preset is built at run time
        self.assertEqual(data["context"]["folders"], [])
        self.assertEqual(data["launch"]["adapter"], "auto")
        self.assertIn(str(self.paths.config_file), out)
        self.assertEqual(self.run_cli("setup", "--yes")[0], 1)  # refuses to overwrite
        self.assertEqual(self.run_cli("setup", "--yes", "--force")[0], 0)

    def test_setup_interactive_answers(self):
        answers = iter(["1234567890", "codex", "omnigent"])  # nothing to discover in the empty home: no 4th question
        with contextlib.redirect_stdout(io.StringIO()):
            code = cli.cmd_setup(Namespace(force=False, yes=False, ask=lambda prompt: next(answers)))
        self.assertEqual(code, 0)
        data = tomllib.loads(self.paths.config_file.read_text())
        self.assertEqual((data["x"]["user_id"], data["enrich"]["agent"], data["launch"]["adapter"]),
                         ("1234567890", "codex", "omnigent"))

    def test_setup_custom_agent_leaves_agent_empty(self):
        answers = iter(["", "custom", "print"])
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_setup(Namespace(force=False, yes=False, ask=lambda prompt: next(answers)))
        self.assertEqual(tomllib.loads(self.paths.config_file.read_text())["enrich"]["agent"], "")

    def test_setup_creates_the_config_readable_by_you_only(self):
        old = os.umask(0o022)  # the usual default would give 0644
        try:
            self.assertEqual(self.run_cli("setup", "--yes")[0], 0)
            self.assertEqual(self.paths.config_file.stat().st_mode & 0o777, 0o600)
            os.chmod(self.paths.config_file, 0o644)
            self.assertEqual(self.run_cli("setup", "--yes", "--force")[0], 0)  # a replaced file is tightened too
            self.assertEqual(self.paths.config_file.stat().st_mode & 0o777, 0o600)
        finally:
            os.umask(old)

    def test_setup_reports_a_value_the_toml_editor_refuses_instead_of_crashing(self):
        from unhoard import omnigent_detect
        with mock.patch.object(omnigent_detect, "setup_step", return_value=("omnigent", {"agent_id": "bad\x7fid"})):
            code, _, err = self.run_cli("setup", "--yes", "--no-discover")
        self.assertEqual(code, 1)
        self.assertIn("could not write", err)
        self.assertFalse(self.paths.config_file.exists())

    def test_setup_never_writes_a_token(self):
        with mock.patch.dict(os.environ, {"X_BEARER_TOKEN": "tok-should-not-appear"}):
            self.run_cli("setup", "--yes")
        self.assertNotIn("tok-should-not-appear", self.paths.config_file.read_text())


class FlowTests(CliCase):
    def test_help_and_version(self):
        with self.assertRaises(SystemExit) as ctx, contextlib.redirect_stdout(io.StringIO()) as out:
            cli.main(["--help"])
        self.assertEqual(ctx.exception.code, 0)
        for word in ("sync", "enrich", "next", "decide", "serve", "status"):
            self.assertIn(word, out.getvalue())

    def test_status_on_a_fresh_install(self):
        code, out, _ = self.run_cli("status")
        info = json.loads(out)
        self.assertEqual((code, info["x_token_set"], info["counts"]["bookmarks"]), (0, False, 0))
        self.assertEqual(info["data_dir"], str(self.paths.data_dir))

    def test_status_warns_about_the_legacy_single_command(self):
        self.write_config('[enrich]\ncommand = ["some-agent"]\n')
        info = json.loads(self.run_cli("status")[1])
        self.assertEqual((info["read_command"], info["relate_command"]), (["some-agent"], ["some-agent"]))
        self.assertTrue(any("deprecated" in w for w in info["enrich_warnings"]))

    def test_enrich_refuses_to_start_with_web_tools_in_the_relate_command(self):
        self.write_config('[enrich]\nread_command = ["claude", "-p", "--tools", "WebFetch"]\n'
                          'relate_command = ["claude", "-p", "--tools", "Read,WebFetch"]\n')
        self.run_cli("sync", "--from-file", str(helpers.FIXTURES / "bookmarks_page1.json"))
        code, out, err = self.run_cli("enrich")
        self.assertEqual(code, 1)
        self.assertIn("refusing to start", err)
        info = json.loads(self.run_cli("status")[1])
        self.assertTrue(any("refusing to start" in w for w in info["enrich_warnings"]))
        self.assertEqual((info["counts"]["pending"], info["counts"]["errors"]), (3, 0))

    def test_sync_from_file_enrich_next_decide(self):
        self.write_config(f'[enrich]\ncommand = [{json.dumps(sys.executable)}, {json.dumps(AGENT)}, "ok"]\n')
        code, out, _ = self.run_cli("sync", "--from-file", str(helpers.FIXTURES / "bookmarks_page1.json"))
        self.assertEqual((code, json.loads(out)["inserted"]), (0, 3))
        code, out, _ = self.run_cli("enrich", "--limit", "2")
        self.assertEqual((code, json.loads(out)["tasks"]), (0, 4))
        code, out, _ = self.run_cli("next", "--json")
        card = json.loads(out)["card"]
        self.assertEqual(card["priority"], "high")
        code, out, _ = self.run_cli("next")
        self.assertIn(card["action_id"], out)
        self.assertEqual(self.run_cli("decide", card["action_id"], "later")[0], 0)
        self.assertEqual(self.run_cli("decide", card["action_id"], "skip")[0], 0)  # snoozed card is still open
        self.assertEqual(self.run_cli("decide", card["action_id"], "skip")[0], 2)  # now decided
        self.assertEqual(self.run_cli("decide", "garbage", "skip")[0], 2)

    def test_go_with_print_adapter_prints_the_brief(self):
        self.run_cli("demo")
        card = json.loads(self.run_cli("next", "--json")[1])["card"]
        code, out, _ = self.run_cli("decide", card["action_id"], "go", "--note", "keep it short")
        self.assertEqual(code, 0)
        self.assertIn("keep it short", out)
        self.assertIn('"launch_status": "printed"', out)

    def test_go_failure_is_reported_and_card_stays(self):
        self.write_config('[launch]\nadapter = "omnigent"\n[omnigent]\nagent_id = "ag"\napi = "http://127.0.0.1:9"\n')
        self.run_cli("demo")
        card = json.loads(self.run_cli("next", "--json")[1])["card"]
        code, _, err = self.run_cli("decide", card["action_id"], "go")
        self.assertEqual(code, 1)
        self.assertIn("back in the queue", err)
        self.assertEqual(json.loads(self.run_cli("next", "--json")[1])["card"]["action_id"], card["action_id"])

    def test_next_on_empty_queue(self):
        code, out, _ = self.run_cli("next")
        self.assertIn("empty", out)

    def test_enrich_without_agent_command_fails_cleanly(self):
        self.run_cli("sync", "--from-file", str(helpers.FIXTURES / "bookmarks_page1.json"))
        code, out, _ = self.run_cli("enrich")
        self.assertEqual(code, 1)
        self.assertIn("no agent command configured", out)

    def test_sync_without_token_fails_cleanly(self):
        code, _, err = self.run_cli("sync")
        self.assertEqual(code, 1)
        self.assertIn("X_BEARER_TOKEN", err)

    def test_bad_config_file(self):
        self.write_config("[oops")
        code, _, err = self.run_cli("status")
        self.assertEqual(code, 2)
        self.assertIn("cannot read", err)

    def test_module_help_runs(self):
        env = {**os.environ, "PYTHONPATH": str(helpers.ROOT / "src")}
        proc = subprocess.run([sys.executable, "-m", "unhoard", "--help"], capture_output=True, text=True, env=env)
        self.assertEqual(proc.returncode, 0)
        self.assertIn("usage: unhoard", proc.stdout)



class ContextDiscoverTests(CliCase):
    def setUp(self):
        super().setUp()
        h = self.home
        (h / ".claude" / "projects" / "-p" / "memory").mkdir(parents=True)
        (h / ".claude" / "projects" / "-p" / "memory" / "MEMORY.md").write_text("- index")
        (h / "Vault" / ".obsidian").mkdir(parents=True)
        (h / "Vault" / "n.md").write_text("n")
        (h / ".claude.json").write_text(json.dumps({"mcpServers": {
            "gbrain": {"type": "http", "url": "https://x"}, "exa": {"type": "http", "url": "https://e"}}}))

    def context(self):
        return tomllib.loads(self.paths.config_file.read_text())["context"]

    def test_setup_yes_discovers_and_writes_explicit_lists(self):
        code, out, _ = self.run_cli("setup", "--yes")
        self.assertEqual(code, 0)
        ctx = self.context()
        self.assertEqual(ctx["memory_folders"], ["~/.claude/projects/-p/memory"])
        self.assertEqual(ctx["notes_folders"], ["~/Vault"])
        self.assertEqual(ctx["index_files"], ["~/.claude/projects/-p/memory/MEMORY.md"])
        self.assertEqual(ctx["mcp_servers"], ["gbrain"])
        self.assertIn("Memory folders", out)
        self.assertIn("not used (unknown servers are never enabled): exa", out)

    def test_setup_no_discover(self):
        self.run_cli("setup", "--yes", "--no-discover")
        self.assertEqual(self.context()["memory_folders"], [])

    def test_setup_asks_once_and_a_no_writes_nothing(self):
        answers = iter(["", "claude", "print", "n"])
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_setup(Namespace(force=False, yes=False, ask=lambda prompt: next(answers)))
        self.assertEqual(self.context()["memory_folders"], [])

    def test_setup_accepting_writes(self):
        answers = iter(["", "claude", "print", ""])  # empty answer = yes
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_setup(Namespace(force=False, yes=False, ask=lambda prompt: next(answers)))
        self.assertEqual(self.context()["mcp_servers"], ["gbrain"])

    def test_context_discover_merges_without_clobbering_and_keeps_comments(self):
        self.run_cli("setup", "--yes", "--no-discover")
        text = self.paths.config_file.read_text().replace("folders = []", 'folders = ["~/mine"]', 1)
        self.paths.config_file.write_text(text.replace("notes_folders = []", 'notes_folders = ["~/my-notes"]'))
        code, out, _ = self.run_cli("context", "discover", "--yes")
        self.assertEqual(code, 0)
        ctx = self.context()
        self.assertEqual(ctx["notes_folders"], ["~/my-notes", "~/Vault"])
        self.assertEqual(self.run_cli("context", "discover", "--yes")[0], 0)
        self.assertEqual(self.context()["notes_folders"], ["~/my-notes", "~/Vault"])  # idempotent
        self.assertIn("# Optional plain-text file describing you", self.paths.config_file.read_text())
        self.assertEqual(tomllib.loads(self.paths.config_file.read_text())["enrich"]["agent"], "claude")

    def test_dry_run_changes_nothing_and_needs_no_config(self):
        code, out, _ = self.run_cli("context", "discover", "--dry-run")
        self.assertEqual(code, 0)
        self.assertIn("~/Vault", out)
        self.assertFalse(self.paths.config_file.exists())

    def test_context_discover_without_a_config_says_to_run_setup(self):
        code, _, err = self.run_cli("context", "discover", "--yes")
        self.assertEqual(code, 1)
        self.assertIn("setup", err)

    def test_status_shows_context_mcp_and_unused_servers(self):
        self.run_cli("setup", "--yes")
        info = json.loads(self.run_cli("status")[1])
        self.assertEqual(info["agent"], "claude")
        self.assertEqual([f["kind"] for f in info["context_in_use"]["folders"]], ["memory", "notes"])
        self.assertEqual(info["mcp"]["in_use"], {"gbrain": list(__import__("unhoard.mcp", fromlist=["x"]).REGISTRY["gbrain"]["tools"])})
        self.assertEqual(info["mcp"]["not_used"], ["exa"])
        self.assertIn("mcp_tools", info["mcp"]["how_to_add_tools"])
        self.assertEqual(info["context_in_use"]["relate_timeout_seconds"], 600)
        self.assertNotIn("https://x", json.dumps(info))  # server entries never appear in status

    def test_status_says_codex_has_no_mcp(self):
        self.write_config('[enrich]\nagent = "codex"\n')
        info = json.loads(self.run_cli("status")[1])
        self.assertIn("not used with agent = codex", info["mcp"]["note"])

    def test_setup_without_a_terminal_and_without_yes_saves_only_repositories(self):
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=False):
            code, out, _ = self.run_cli("setup")
        self.assertEqual(code, 0)
        ctx = self.context()
        self.assertEqual(ctx["memory_folders"], [])  # agent memory needs a person to accept it
        self.assertEqual(ctx["mcp_servers"], [])
        self.assertEqual(ctx["index_files"], [])  # the memory folder's MEMORY.md stays out with it
        self.assertEqual(ctx["notes_folders"], [])  # notes vaults need a person too
        self.assertEqual(ctx["index_files"], [])  # so do the global CLAUDE.md / AGENTS.md
        self.assertIn("NOT saved: memory_folders (1), notes_folders (1), index_files", out)
        self.assertIn("mcp_servers (1)", out)
        self.assertIn("--yes", out)

    def test_context_discover_unattended_is_the_same_and_yes_still_saves_everything(self):
        self.run_cli("setup", "--yes", "--no-discover")
        with mock.patch.object(cli.sys.stdin, "isatty", return_value=False):
            self.assertEqual(self.run_cli("context", "discover")[0], 0)
        self.assertEqual(self.context()["mcp_servers"], [])
        self.assertEqual(self.run_cli("context", "discover", "--yes")[0], 0)
        self.assertEqual(self.context()["mcp_servers"], ["gbrain"])
        self.assertEqual(self.context()["memory_folders"], ["~/.claude/projects/-p/memory"])

    def test_a_terminal_still_asks(self):
        asked = []
        answers = iter(["", "claude", "print", "y"])

        def ask(prompt):
            asked.append(prompt)
            return next(answers)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_setup(Namespace(force=False, yes=False, ask=ask))
        self.assertIn("\nUse these? [Y/n]: ", asked)
        self.assertEqual(self.context()["mcp_servers"], ["gbrain"])

    def test_config_is_written_atomically_and_keeps_other_keys(self):
        self.run_cli("setup", "--yes", "--no-discover")
        before = tomllib.loads(self.paths.config_file.read_text())
        mode_before = self.paths.config_file.stat().st_mode & 0o777
        self.run_cli("context", "discover", "--yes")
        after = tomllib.loads(self.paths.config_file.read_text())
        for section in before:
            for key, value in before[section].items():
                if section != "context" or key not in ("memory_folders", "notes_folders", "index_files", "repos", "mcp_servers"):
                    self.assertEqual(after[section][key], value, (section, key))
        self.assertEqual(self.paths.config_file.stat().st_mode & 0o777, mode_before)
        self.assertEqual([p.name for p in self.paths.config_file.parent.iterdir()], ["config.toml"])  # no temp file left


class SyncRefreshCliTests(CliCase):
    def test_sync_refresh_from_file_updates_display_fields_only(self):
        rich = str(helpers.FIXTURES / "bookmarks_rich.json")
        self.assertEqual(self.run_cli("sync", "--from-file", rich)[0], 0)
        db = helpers_connect(self.paths)
        db.execute("UPDATE bookmarks SET author_avatar_url=NULL, text_display=NULL, metrics_json=NULL")
        db.execute("UPDATE bookmarks SET text='edited by hand' WHERE tweet_id='205'")
        db.commit()
        db.close()
        code, out, _ = self.run_cli("sync", "--refresh", "--from-file", rich)
        self.assertEqual((code, json.loads(out)), (0, {"seen": 3, "updated": 3}))
        db = helpers_connect(self.paths)
        row = db.execute("SELECT * FROM bookmarks WHERE tweet_id='205'").fetchone()
        db.close()
        self.assertTrue(row["author_avatar_url"].endswith("_400x400.jpg"))
        self.assertEqual(row["text"], "edited by hand")  # the enrichment input is not touched

    def test_sync_refresh_without_a_token_fails_cleanly(self):
        code, _, err = self.run_cli("sync", "--refresh")
        self.assertEqual(code, 1)
        self.assertIn("X_BEARER_TOKEN", err)

    def test_help_has_no_bundle_command(self):
        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()) as out:
            cli.main(["--help"])
        self.assertNotIn("bundle", out.getvalue())
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cli.main(["bundle", "install", "x"])


def helpers_connect(paths):
    from unhoard.store import connect
    return connect(paths)


if __name__ == "__main__":
    unittest.main()
