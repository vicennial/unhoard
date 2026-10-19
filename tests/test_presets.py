import helpers
import json
import sys
import unittest
from pathlib import Path

from unhoard import agents, enrich, sync


class PresetArgvTests(unittest.TestCase):
    def test_claude_read_preset(self):
        self.assertEqual(agents.claude_read_command(),
                         ["claude", "-p", "--output-format", "json", "--tools", "WebFetch,WebSearch",
                          "--allowedTools", "WebFetch,WebSearch", "--permission-mode", "dontAsk", "--strict-mcp-config",
                          "--setting-sources", "", "--disable-slash-commands", "--no-chrome"])

    def test_claude_relate_preset_without_mcp_allows_nothing_by_name(self):
        argv = agents.claude_relate_command(folders=[Path("/a"), Path("/b")])
        self.assertEqual(argv, ["claude", "-p", "--output-format", "json", "--tools", "Read,Grep,Glob",
                                "--permission-mode", "dontAsk", "--strict-mcp-config",
                                "--setting-sources", "", "--disable-slash-commands", "--no-chrome",
                                "--add-dir", "/a", "--add-dir", "/b"])
        # blanket Read in --allowedTools would let it read any path on disk, not only the --add-dir folders
        self.assertNotIn("--allowedTools", argv)

    def test_claude_relate_preset_with_mcp(self):
        argv = agents.claude_relate_command(["mcp__gbrain__query", "mcp__gbrain__search"], "/tmp/x/mcp.json", [Path("/a")])
        self.assertEqual(argv[argv.index("--allowedTools") + 1], "mcp__gbrain__query,mcp__gbrain__search")
        self.assertEqual(argv[argv.index("--mcp-config") + 1], "/tmp/x/mcp.json")
        self.assertIn("--strict-mcp-config", argv)
        self.assertEqual(argv[argv.index("--permission-mode") + 1], "dontAsk")
        for word in ("WebFetch", "WebSearch", "Bash"):
            self.assertNotIn(word, " ".join(argv))

    def test_both_claude_presets_load_no_user_customization(self):
        """--setting-sources "" drops settings, CLAUDE.md, hooks, plugins and allow rules; verified live (see doctor)."""
        for argv in (agents.claude_read_command(), agents.claude_relate_command(["mcp__g__q"], "/x/m.json")):
            i = argv.index("--setting-sources")
            self.assertEqual(argv[i + 1], "")
            self.assertIn("--disable-slash-commands", argv)
            self.assertIn("--no-chrome", argv)
            self.assertNotIn("--chrome", argv)
            self.assertTrue(agents.has_isolation(argv))
            for bad in ("--bare", "--safe-mode", "--settings", "--plugin-dir"):
                self.assertNotIn(bad, argv)  # --safe-mode drops --mcp-config servers; --bare needs an API key

    def test_relate_preset_denies_unselected_tools_of_enabled_servers(self):
        argv = agents.claude_relate_command(["mcp__g__q"], "/x/m.json", deny_tools=["mcp__g__put_page", "mcp__g__x"])
        self.assertEqual(argv[argv.index("--disallowedTools") + 1], "mcp__g__put_page,mcp__g__x")
        self.assertNotIn("--disallowedTools", agents.claude_relate_command(["mcp__g__q"], "/x/m.json"))
        flags = agents.parse_flags(argv)
        self.assertEqual(flags["--allowedTools"], ["mcp__g__q"])

    def test_claude_presets_drop_environment_plugins_and_disable_auto_memory(self):
        env = agents.agent_env({"CLAUDE_CODE_PLUGIN_DIRS": "/p", "X_BEARER_TOKEN": "t", "KEEP": "1"},
                               agents.claude_read_command(), "X_BEARER_TOKEN")
        self.assertEqual(env, {"KEEP": "1", **agents.CLAUDE_ENV_SET})
        self.assertEqual(agents.CLAUDE_ENV_SET, {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1",
                                                 "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
                                                 "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
                                                 "CLAUDE_CODE_ENABLE_CFC": "0"})
        other = agents.agent_env({"CLAUDE_CODE_PLUGIN_DIRS": "/p", "X_BEARER_TOKEN": "t"}, ["my-agent"], "X_BEARER_TOKEN")
        self.assertEqual(other, {"CLAUDE_CODE_PLUGIN_DIRS": "/p"})  # only claude commands are touched

    def test_claude_presets_turn_claude_in_chrome_off_in_the_environment(self):
        base = {"CLAUDE_CODE_ENABLE_CFC": "1", "ENABLE_CLAUDEAI_MCP_SERVERS": "true", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "0"}
        for argv in (agents.claude_read_command(), agents.claude_relate_command()):
            env = agents.agent_env(base, argv, "X_BEARER_TOKEN")
            self.assertEqual(env["CLAUDE_CODE_ENABLE_CFC"], "0")  # explicit false, not merely unset
            self.assertEqual(env["ENABLE_CLAUDEAI_MCP_SERVERS"], "false")
            self.assertEqual(env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"], "1")
        self.assertEqual(agents.agent_env(base, ["my-agent"], "X_BEARER_TOKEN"), base)
        # a custom direct claude command gets the same environment
        custom = agents.agent_env(base, ["/usr/bin/claude", "-p"], "X_BEARER_TOKEN")
        self.assertEqual(custom["CLAUDE_CODE_ENABLE_CFC"], "0")

    def test_codex_presets_remove_web_mcp_and_other_tools(self):
        for argv in (agents.codex_read_command(), agents.codex_relate_command()):
            self.assertEqual(argv[:2], ["codex", "exec"])
            self.assertIn("--ignore-user-config", argv)
            self.assertIn('web_search="disabled"', argv)
            self.assertIn("--ignore-rules", argv)  # no execpolicy .rules files
            self.assertEqual(argv[-1], "-")
            off = [argv[i + 1] for i, a in enumerate(argv) if a == "--disable"]
            for name in ("browser_use", "browser_use_external", "computer_use", "apps", "image_generation", "multi_agent"):
                self.assertIn(name, off)
            self.assertEqual(argv[argv.index("--sandbox") + 1], "read-only")
        read_off = [a for a in agents.codex_read_command()]
        self.assertIn("shell_tool", read_off)
        self.assertIn("unified_exec", read_off)
        self.assertNotIn("shell_tool", agents.codex_relate_command())  # relate keeps its sandboxed shell

    def test_old_preset_shapes_are_recognised_exactly(self):
        c = ["claude", "-p", "--output-format", "json", "--tools"]
        self.assertEqual(agents.old_preset(c + ["WebFetch,WebSearch"], "read"), "claude")
        self.assertEqual(agents.old_preset(c + ["WebFetch,WebSearch", "--strict-mcp-config"], "read"), "claude")
        self.assertEqual(agents.old_preset(c + ["Read,Grep,Glob"], "relate"), "claude")
        self.assertEqual(agents.old_preset(c + ["Read,Grep,Glob", "--strict-mcp-config"], "relate"), "claude")
        self.assertEqual(agents.old_preset(["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only",
                                            "--ephemeral", "-"], "read"), "codex")
        # a relate-shaped command in the read slot, or any edit, is not an old preset
        self.assertIsNone(agents.old_preset(c + ["Read,Grep,Glob"], "read"))
        self.assertIsNone(agents.old_preset(c + ["Read,Grep,Glob", "--strict-mcp-config", "--add-dir", "/x"], "relate"))
        self.assertIsNone(agents.old_preset(c + ["Read,Grep,Glob,WebFetch"], "relate"))


class PlanTests(helpers.TempCase):
    def plan(self, enrich_cfg=None, context=None, servers=None):
        with helpers.fake_home(self.tmp / "home", servers):
            return enrich.command_plan(self.config(enrich=enrich_cfg or {}, context=context or {}))

    def test_agent_claude_builds_both_commands_at_run_time(self):
        plan = self.plan({"agent": "claude"})
        self.assertEqual((plan["read_kind"], plan["relate_kind"], plan["warnings"]), ("claude", "claude", []))
        self.assertEqual(plan["read"], agents.claude_read_command())
        self.assertEqual(plan["relate"], agents.claude_relate_command())

    def test_agent_codex(self):
        plan = self.plan({"agent": "codex"})
        self.assertEqual((plan["read"], plan["relate"]), (agents.codex_read_command(), agents.codex_relate_command()))

    def test_custom_command_overrides_the_preset_for_that_pass(self):
        plan = self.plan({"agent": "claude", "read_command": ["my-reader"]})
        self.assertEqual((plan["read_kind"], plan["read"], plan["relate_kind"]), ("custom", ["my-reader"], "claude"))

    def test_older_preset_argv_in_the_config_is_treated_as_the_preset_without_refusal(self):
        c = ["claude", "-p", "--output-format", "json", "--tools"]
        for read, relate in ((c + ["WebFetch,WebSearch"], c + ["Read,Grep,Glob"]),
                             (c + ["WebFetch,WebSearch", "--strict-mcp-config"], c + ["Read,Grep,Glob", "--strict-mcp-config"])):
            plan = self.plan({"read_command": read, "relate_command": relate})
            self.assertEqual((plan["read_kind"], plan["relate_kind"], plan["warnings"]), ("claude", "claude", []))
            self.assertEqual((plan["read"], plan["relate"]), (agents.claude_read_command(), agents.claude_relate_command()))

    def test_older_codex_argv_becomes_the_codex_preset(self):
        old = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "-"]
        plan = self.plan({"read_command": old, "relate_command": old})
        self.assertEqual((plan["read"], plan["relate"]), (agents.codex_read_command(), agents.codex_relate_command()))

    def test_mcp_servers_in_context_become_allowed_tools_and_the_status_shows_unused_ones(self):
        plan = self.plan({"agent": "claude"}, {"mcp_servers": ["gbrain", "notes-graph"]}, helpers.CLAUDE_SERVERS)
        sel = plan["mcp"]
        self.assertEqual(sorted(sel.servers), ["gbrain", "notes-graph"])
        self.assertIn("mcp__gbrain__query", sel.tools)
        self.assertIn("mcp__notes-graph__search_notes", sel.tools)
        self.assertEqual(sel.not_used, ["exa", "memory"])
        self.assertIn("--allowedTools", plan["relate"])
        self.assertNotIn(helpers.SECRET, json.dumps(plan["relate"]) + json.dumps(sel.summary()) + repr(sel))

    def test_codex_ignores_mcp_with_a_warning(self):
        plan = self.plan({"agent": "codex"}, {"mcp_servers": ["gbrain"]}, helpers.CLAUDE_SERVERS)
        self.assertTrue(any("not used with agent = codex" in w for w in plan["warnings"]))
        self.assertNotIn("--mcp-config", plan["relate"])


class CustomGuardTests(helpers.TempCase):
    def check(self, relate, **over):
        cfg = self.config(enrich={"read_command": ["read-agent"], "relate_command": relate, **over})
        return enrich.command_plan(cfg)

    def refused(self, relate):
        with self.assertRaises(enrich.EnrichmentError) as ctx:
            self.check(relate)
        return str(ctx.exception)

    BASE = ["claude", "-p", "--tools", "Read,Grep", "--strict-mcp-config", "--setting-sources", "",
            "--disable-slash-commands"]

    def test_custom_mcp_config_needs_dont_ask_and_an_allowed_tools_list(self):
        self.assertIn("--permission-mode dontAsk", self.refused(self.BASE + ["--mcp-config", "m.json"]))
        self.assertIn("--allowedTools", self.refused(self.BASE + ["--mcp-config", "m.json", "--permission-mode", "dontAsk"]))

    def test_custom_mcp_config_with_web_or_bash_in_allowed_tools_is_refused(self):
        ok = self.BASE + ["--mcp-config", "m.json", "--permission-mode", "dontAsk"]
        for allowed in ("mcp__x__q,WebFetch", "Bash(git:*)", "mcp__x__q,Bash", "WebSearch"):
            self.assertIn("refusing to start", self.refused(ok + ["--allowedTools", allowed]))

    def test_custom_mcp_config_that_passes_gets_an_unverifiable_warning(self):
        plan = self.check(self.BASE + ["--mcp-config", "m.json", "--permission-mode", "dontAsk",
                                       "--allowedTools", "mcp__x__q", "mcp__x__r"])
        self.assertTrue(any("cannot be verified" in w for w in plan["warnings"]))

    def test_equals_forms_are_understood(self):
        plan = self.check(["claude", "-p", "--tools=Read", "--strict-mcp-config", "--mcp-config=m.json",
                           "--permission-mode=dontAsk", "--allowedTools=mcp__x__q"])
        self.assertTrue(plan["warnings"])

    def test_custom_relate_without_strict_mcp_config_is_still_refused(self):
        self.assertIn("--strict-mcp-config", self.refused(["claude", "-p", "--tools", "Read"]))

    def test_presets_never_trigger_a_refusal(self):
        for agent in ("claude", "codex"):
            cfg = self.config(enrich={"agent": agent})
            self.assertEqual(enrich.command_plan(cfg)["warnings"], [])


if __name__ == "__main__":
    unittest.main()
