import helpers
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from unhoard import agents, enrich, mcp, sync


class RegistryTests(unittest.TestCase):
    def test_matching_by_name_command_args_and_url(self):
        servers = helpers.CLAUDE_SERVERS
        self.assertEqual(mcp.match_registry("gbrain", servers["gbrain"]), "gbrain")
        self.assertEqual(mcp.match_registry("notes-graph", servers["notes-graph"]), "basic-memory")  # by args
        self.assertEqual(mcp.match_registry("memory", servers["memory"]), "memory")
        self.assertEqual(mcp.match_registry("brain", {"type": "http", "url": "https://x.example.invalid/gbrain/mcp"}), "gbrain")
        self.assertEqual(mcp.match_registry("mem", {"command": "npx", "args": ["@modelcontextprotocol/server-memory"]}), "memory")
        self.assertIsNone(mcp.match_registry("exa", servers["exa"]))
        self.assertIsNone(mcp.match_registry("linear", {"type": "http", "url": "https://mcp.linear.example.invalid"}))

    def test_registry_lists_only_read_only_tools(self):
        forbidden = ("fetch", "think", "synthesize", "file_", "put_", "capture", "remember", "submit", "job", "delete",
                     "add_", "remove", "forget", "sync", "write", "create", "update")
        for key, entry in mcp.REGISTRY.items():
            for tool in entry["tools"]:
                self.assertFalse(any(word in tool for word in forbidden), f"{key}.{tool}")
        self.assertEqual(mcp.REGISTRY["gbrain"]["tools"],
                         ("query", "search", "get_page", "get_links", "get_backlinks", "get_timeline", "list_pages",
                          "resolve_slugs"))
        self.assertEqual(mcp.REGISTRY["basic-memory"]["tools"], ("search_notes", "read_note", "build_context", "recent_activity"))
        self.assertEqual(mcp.REGISTRY["memory"]["tools"], ("read_graph", "search_nodes", "open_nodes"))

    def test_server_names_are_normalised_like_claude_does(self):
        self.assertEqual(mcp.tool_name("my server.v2", "query"), "mcp__my_server_v2__query")
        self.assertEqual(mcp.tool_name("personal-mail", "x"), "mcp__personal-mail__x")


class SelectionTests(unittest.TestCase):
    S = helpers.CLAUDE_SERVERS

    def test_only_enabled_registry_servers_and_their_registry_tools(self):
        sel = mcp.select(["gbrain"], [], self.S)
        self.assertEqual(list(sel.servers), ["gbrain"])
        self.assertEqual(sel.tools, [f"mcp__gbrain__{t}" for t in mcp.REGISTRY["gbrain"]["tools"]])
        self.assertEqual(sel.not_used, ["exa", "memory", "notes-graph"])
        self.assertNotIn("mcp__gbrain__fetch", sel.tools)

    def test_unknown_enabled_server_gets_no_tools(self):
        sel = mcp.select(["exa"], [], self.S)
        self.assertFalse(sel)
        self.assertEqual(sel.tools, [])

    def test_manual_tools_pull_in_their_server(self):
        sel = mcp.select([], ["mcp__exa__search_notes_local"], self.S)
        self.assertEqual((list(sel.servers), sel.tools), (["exa"], ["mcp__exa__search_notes_local"]))

    def test_missing_servers_are_reported(self):
        sel = mcp.select(["gone"], ["mcp__nothere__q"], self.S)
        self.assertEqual(sel.missing, ["gone", "mcp__nothere__q"])

    def test_user_servers_come_from_the_top_level_user_scope_only(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / ".claude.json").write_text(json.dumps({
                "mcpServers": {"gbrain": {"type": "http", "url": "https://x"}},
                "projects": {"/p": {"mcpServers": {"proj-only": {"type": "http", "url": "https://y"}}}}}))
            self.assertEqual(list(mcp.load_user_servers({}, home)), ["gbrain"])
            self.assertEqual(mcp.load_user_servers({}, home / "nowhere"), {})
            (home / ".claude.json").write_text("{not json")
            self.assertEqual(mcp.load_user_servers({}, home), {})
            self.assertEqual(mcp.claude_config_path({"CLAUDE_CONFIG_DIR": "/c"}, home), Path("/c/.claude.json"))


class ConfigFileTests(unittest.TestCase):
    def test_file_has_only_enabled_servers_mode_0600_and_is_deleted(self):
        sel = mcp.select(["gbrain"], [], helpers.CLAUDE_SERVERS)
        with mcp.config_file(sel) as path:
            mode = stat.S_IMODE(os.stat(path).st_mode)
            folder_mode = stat.S_IMODE(os.stat(os.path.dirname(path)).st_mode)
            data = json.loads(Path(path).read_text())
        self.assertEqual((mode, folder_mode), (0o600, 0o700))
        self.assertEqual(list(data["mcpServers"]), ["gbrain"])
        self.assertEqual(data["mcpServers"]["gbrain"], helpers.CLAUDE_SERVERS["gbrain"])
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(os.path.dirname(path)))

    def test_file_is_deleted_when_the_run_fails(self):
        sel = mcp.select(["gbrain"], [], helpers.CLAUDE_SERVERS)
        seen = []
        with self.assertRaises(RuntimeError):
            with mcp.config_file(sel) as path:
                seen.append(path)
                raise RuntimeError("boom")
        self.assertFalse(os.path.exists(seen[0]))

    def test_nothing_enabled_writes_no_file(self):
        with mcp.config_file(mcp.McpSelection()) as path:
            self.assertIsNone(path)

    def test_selection_repr_hides_server_entries(self):
        sel = mcp.select(["gbrain"], [], helpers.CLAUDE_SERVERS)
        self.assertNotIn(helpers.SECRET, repr(sel))
        self.assertNotIn(helpers.SECRET, json.dumps(sel.summary()))


class EnrichWithMcpTests(helpers.TempCase):
    def setUp(self):
        super().setUp()
        sync.ingest_payload(self.db, helpers.fixture("bookmarks_page1.json"))
        self.memory = self.tmp / "mem"
        self.notes = self.tmp / "vault"
        self.repo = self.tmp / "code"
        for d in (self.memory, self.notes, self.repo):
            d.mkdir()
        (self.memory / "MEMORY.md").write_text("- reader builds a slow CI pipeline")
        self.calls = []

    def runner(self, argv, prompt, timeout):
        i = len(self.calls)
        self.calls.append({"argv": argv, "prompt": prompt, "timeout": timeout})
        if "--mcp-config" in argv:
            path = argv[argv.index("--mcp-config") + 1]
            self.calls[-1]["mcp_file"] = json.loads(Path(path).read_text())
            self.calls[-1]["mcp_path"] = path
            self.calls[-1]["mcp_mode"] = stat.S_IMODE(os.stat(path).st_mode)
        if i % 2 == 0:
            return json.dumps({"summary": "s", "topics": ["ci speed"], "link_summary": "page text", "kind": "tool"})
        return json.dumps({"why_saved": "links to MEMORY.md CI note", "confidence": 0.9, "tasks": []})

    def cfg(self, **context):
        return self.config(enrich={"agent": "claude"},
                           context={"memory_folders": [str(self.memory)], "notes_folders": [str(self.notes)],
                                    "repos": [str(self.repo)], "index_files": [str(self.memory / "MEMORY.md")],
                                    **context})

    def run_one(self, cfg):
        with helpers.fake_home(self.tmp / "home", helpers.CLAUDE_SERVERS):
            plan = enrich.command_plan(cfg)
            row = self.db.execute("SELECT * FROM bookmarks LIMIT 1").fetchone()
            enrich.enrich_one(self.db, row, cfg, self.runner, plan)

    def test_relate_run_gets_folders_mcp_file_tools_and_the_investigate_prompt(self):
        self.run_one(self.cfg(mcp_servers=["gbrain"]))
        read, relate = self.calls
        self.assertEqual(read["argv"], agents.claude_read_command())
        argv = relate["argv"]
        self.assertEqual(argv[:3], ["claude", "-p", "--output-format"])
        self.assertEqual([argv[i + 1] for i, a in enumerate(argv) if a == "--add-dir"],
                         [str(self.memory.resolve()), str(self.notes.resolve()), str(self.repo.resolve())])
        allowed = argv[argv.index("--allowedTools") + 1].split(",")
        self.assertEqual(allowed, [f"mcp__gbrain__{t}" for t in mcp.REGISTRY["gbrain"]["tools"]])
        self.assertEqual(list(relate["mcp_file"]["mcpServers"]), ["gbrain"])  # not exa, memory or notes-graph
        self.assertEqual(relate["mcp_mode"], 0o600)
        self.assertFalse(os.path.exists(relate["mcp_path"]))  # deleted after the run
        p = relate["prompt"]
        self.assertIn(f"- {self.memory.resolve()} (agent memory)", p)
        self.assertIn(f"- {self.notes.resolve()} (notes vault)", p)
        self.assertIn(f"- {self.repo.resolve()} (Git repository)", p)
        self.assertIn("reader builds a slow CI pipeline", p)  # the index file is inlined
        self.assertIn("- mcp__gbrain__query", p)
        self.assertNotIn("mcp__gbrain__fetch", p)
        self.assertIn("INVESTIGATE BEFORE YOU ANSWER", p)
        self.assertIn("name the concrete link", p)
        self.assertEqual(relate["timeout"], 600)
        self.assertNotIn(helpers.SECRET, relate["prompt"])

    def test_tools_seen_by_the_doctor_but_not_selected_are_denied_in_the_run(self):
        mcp.save_exposed(self.paths.data_dir, ["mcp__gbrain__query", "mcp__gbrain__put_page", "mcp__gbrain__submit_job"])
        self.run_one(self.cfg(mcp_servers=["gbrain"]))
        argv = self.calls[1]["argv"]
        denied = argv[argv.index("--disallowedTools") + 1].split(",")
        self.assertEqual(sorted(denied), ["mcp__gbrain__put_page", "mcp__gbrain__submit_job"])
        self.assertNotIn("mcp__gbrain__put_page", argv[argv.index("--allowedTools") + 1])
        self.assertIn("--setting-sources", argv)  # the user's allow rules are not loaded either

    def test_without_mcp_servers_there_is_no_mcp_config_or_allowed_tools(self):
        self.run_one(self.cfg())
        argv = self.calls[1]["argv"]
        for flag in ("--mcp-config", "--allowedTools"):
            self.assertNotIn(flag, argv)
        self.assertIn("--strict-mcp-config", argv)
        self.assertNotIn("MEMORY TOOLS", self.calls[1]["prompt"])

    def test_presets_run_in_the_scratch_directory(self):
        seen = []
        orig = enrich.run_agent
        try:
            enrich.run_agent = lambda argv, prompt, timeout, cwd=None: seen.append(cwd) or (
                json.dumps({"summary": "s", "topics": [], "link_summary": "", "kind": "other"}) if len(seen) == 1
                else json.dumps({"why_saved": "w", "confidence": 1, "tasks": []}))
            self.run_one_real(self.cfg())
        finally:
            enrich.run_agent = orig
        self.assertEqual(seen, [str(self.paths.scratch_dir)] * 2)

    def run_one_real(self, cfg):
        with helpers.fake_home(self.tmp / "home", helpers.CLAUDE_SERVERS):
            row = self.db.execute("SELECT * FROM bookmarks LIMIT 1").fetchone()
            enrich.enrich_one(self.db, row, cfg)

    def test_link_summary_is_stored_for_the_brief(self):
        self.run_one(self.cfg())
        row = self.db.execute("SELECT summary, link_summary FROM enrichments").fetchone()
        self.assertEqual((row["summary"], row["link_summary"]), ("s", "page text"))

    def test_codex_preset_argv_and_no_mcp(self):
        cfg = self.config(enrich={"agent": "codex"}, context={"mcp_servers": ["gbrain"], "folders": [str(self.memory)]})
        self.run_one(cfg)
        self.assertEqual(self.calls[0]["argv"], agents.codex_read_command())
        self.assertEqual(self.calls[1]["argv"], agents.codex_relate_command())
        self.assertNotIn("MEMORY TOOLS", self.calls[1]["prompt"])


class ManualToolTests(unittest.TestCase):
    S = helpers.CLAUDE_SERVERS

    def test_server_wide_and_wildcard_manual_tools_are_rejected(self):
        bad = ["mcp__gbrain", "mcp__gbrain__*", "mcp__gbrain__", "mcp__*", "mcp__exa__*", "mcp__exa"]
        sel = mcp.select([], bad + ["mcp__exa__web_search"], self.S)
        self.assertEqual(sel.tools, ["mcp__exa__web_search"])
        self.assertEqual(sel.rejected, sorted(bad))
        self.assertEqual(list(sel.servers), ["exa"])  # only the server of the one valid tool is loaded
        self.assertTrue(any("whole server" in w for w in sel.warnings))
        self.assertEqual(sel.summary()["rejected_tools"], sorted(bad))

    def test_entries_are_split_like_allowed_tools_before_checking(self):
        sel = mcp.select([], ["mcp__exa__web_search, mcp__exa__crawl", "mcp__gbrain__query mcp__gbrain__search"], self.S)
        self.assertEqual(sel.tools, ["mcp__exa__web_search", "mcp__exa__crawl", "mcp__gbrain__query", "mcp__gbrain__search"])
        self.assertEqual(sel.rejected, [])
        # a server-wide form hidden in a comma list is still found
        sel = mcp.select([], ["mcp__exa__web_search,mcp__gbrain"], self.S)
        self.assertEqual((sel.tools, sel.rejected), (["mcp__exa__web_search"], ["mcp__gbrain"]))

    def test_entries_with_separators_whitespace_or_specifiers_are_rejected(self):
        bad = ["mcp__exa__web_search(query x)", "mcp__exa__web_search(a,b)", "mcp__exa__web(search)", "mcp__exa__web*search"]
        sel = mcp.select([], bad, self.S)
        self.assertEqual(sel.tools, [])
        self.assertFalse(sel)
        self.assertEqual(sorted(sel.rejected), sorted(bad))
        # no argv built from a selection can carry a comma inside one name
        sel = mcp.select([], ["mcp__exa__a,mcp__exa__b"], self.S)
        self.assertTrue(all("," not in t and " " not in t for t in sel.tools))

    def test_only_rejected_tools_enable_nothing(self):
        sel = mcp.select([], ["mcp__gbrain", "mcp__gbrain__*"], self.S)
        self.assertFalse(sel)
        self.assertEqual(sel.servers, {})

    def test_the_plan_warns_about_rejected_tools(self):
        with helpers.fake_home(Path(tempfile.mkdtemp()), self.S):
            plan = enrich.command_plan(helpers.Config({"enrich": {"agent": "claude"},
                                                      "context": {"mcp_tools": ["mcp__gbrain__*"]}},
                                                     helpers.Paths(Path("/tmp/x"), Path("/tmp/x/c.toml")), env={}))
        self.assertTrue(any("wildcard" in w for w in plan["warnings"]))


class NameOnlyMatchTests(unittest.TestCase):
    def test_pattern_confirmation(self):
        confirmed = mcp.match_registry_detail("gbrain", helpers.CLAUDE_SERVERS["gbrain"])
        self.assertEqual(confirmed, ("gbrain", True))  # the url path says gbrain
        by_name = mcp.match_registry_detail("memory", {"type": "stdio", "command": "my-own-thing"})
        self.assertEqual(by_name, ("memory", False))
        self.assertEqual(mcp.match_registry_detail("x", {"command": "uvx", "args": ["basic-memory"]}), ("basic-memory", True))
        self.assertEqual(mcp.match_registry_detail("exa", helpers.CLAUDE_SERVERS["exa"]), (None, False))

    def test_a_name_only_match_is_used_but_warned_about(self):
        servers = {"memory": {"type": "stdio", "command": "my-own-thing"}, "gbrain": helpers.CLAUDE_SERVERS["gbrain"]}
        sel = mcp.select(["memory", "gbrain"], [], servers)
        self.assertEqual(sorted(sel.servers), ["gbrain", "memory"])
        self.assertEqual(len(sel.warnings), 1)
        self.assertIn("'memory'", sel.warnings[0])
        self.assertIn("name only", sel.warnings[0])
        self.assertEqual(sel.summary()["warnings"], sel.warnings)


class SignalCleanupTests(unittest.TestCase):
    CODE = """
import json, os, signal, sys, time
sys.path.insert(0, {src!r})
from unhoard import mcp
sel = mcp.McpSelection({{"s": {{"command": "x"}}}}, ["mcp__s__q"])
with mcp.config_file(sel) as path:
    print(path, flush=True)
    time.sleep(60)
"""

    def run_and_signal(self, signum):
        import subprocess
        import time as t
        proc = subprocess.Popen([sys.executable, "-c", self.CODE.format(src=str(helpers.ROOT / "src"))],
                                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
        self.addCleanup(proc.stdout.close)
        self.addCleanup(lambda: proc.poll() is None and proc.kill())
        path = proc.stdout.readline().strip()
        self.assertTrue(os.path.exists(path))
        t.sleep(0.2)
        proc.send_signal(signum)
        code = proc.wait(timeout=10)
        return path, code

    def test_sigterm_deletes_the_generated_mcp_file(self):
        import signal
        path, code = self.run_and_signal(signal.SIGTERM)
        self.assertFalse(os.path.exists(path))
        self.assertFalse(os.path.exists(os.path.dirname(path)))
        self.assertEqual(code, 128 + signal.SIGTERM)

    def test_sigint_deletes_it_too(self):
        import signal
        path, code = self.run_and_signal(signal.SIGINT)
        self.assertFalse(os.path.exists(path))
        self.assertNotEqual(code, 0)

    def test_handlers_are_restored_afterwards(self):
        import signal
        before = signal.getsignal(signal.SIGTERM)
        with mcp.config_file(mcp.McpSelection({"s": {"command": "x"}}, ["mcp__s__q"])):
            self.assertNotEqual(signal.getsignal(signal.SIGTERM), before)
        self.assertEqual(signal.getsignal(signal.SIGTERM), before)

    def test_off_the_main_thread_it_still_works_without_handlers(self):
        import threading
        out = {}

        def work():
            with mcp.config_file(mcp.McpSelection({"s": {"command": "x"}}, ["mcp__s__q"])) as path:
                out["path"] = path
        t = threading.Thread(target=work)
        t.start()
        t.join(10)
        self.assertFalse(os.path.exists(out["path"]))


class DenyToolsTests(unittest.TestCase):
    def test_unselected_tools_of_enabled_servers_are_denied(self):
        sel = mcp.select(["gbrain"], [], helpers.CLAUDE_SERVERS)
        exposed = {"gbrain": ["mcp__gbrain__query", "mcp__gbrain__put_page", "mcp__gbrain__submit_job"],
                   "exa": ["mcp__exa__web_search"]}
        self.assertEqual(mcp.deny_tools(sel, exposed), ["mcp__gbrain__put_page", "mcp__gbrain__submit_job"])
        self.assertEqual(mcp.deny_tools(sel, {}), [])

    def test_exposed_cache_round_trip_keeps_names_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            mcp.save_exposed(Path(tmp), ["Read", "mcp__gbrain__query", "mcp__my_srv__a__b", "mcp__x"])
            self.assertEqual(mcp.load_exposed(Path(tmp)), {"gbrain": ["mcp__gbrain__query"], "my_srv": ["mcp__my_srv__a__b"]})
            mcp.save_exposed(Path(tmp), ["mcp__gbrain__query"])  # a later run saw fewer tools: nothing is forgotten
            mcp.save_exposed(Path(tmp), ["mcp__gbrain__other"])
            self.assertEqual(mcp.load_exposed(Path(tmp))["gbrain"], ["mcp__gbrain__query", "mcp__gbrain__other"])
            self.assertEqual(mcp.load_exposed(Path(tmp) / "nowhere"), {})
            (Path(tmp) / "mcp-tools.json").write_text("not json")
            self.assertEqual(mcp.load_exposed(Path(tmp)), {})


if __name__ == "__main__":
    unittest.main()
