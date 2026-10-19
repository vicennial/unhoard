import helpers
import io
import contextlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

from unhoard import agents, cli, enrich, sync

ISO = ["--setting-sources", "", "--disable-slash-commands"]
RELATE = ["claude", "-p", "--output-format", "json", "--tools", "Read,Grep,Glob", "--strict-mcp-config"] + ISO
READ = ["claude", "-p", "--output-format", "json", "--tools", "WebFetch,WebSearch", "--strict-mcp-config"] + ISO


class FlagParsingTests(unittest.TestCase):
    def test_variadic_tools_read_every_value_until_the_next_flag(self):
        self.assertEqual(agents.claude_tools(["claude", "--tools", "Read", "WebFetch", "Bash", "-p"]), ["Read", "WebFetch", "Bash"])
        self.assertEqual(agents.claude_tools(["claude", "--tools", "Read,Grep", "Glob"]), ["Read", "Grep", "Glob"])
        self.assertEqual(agents.claude_tools(["claude", "--tools=Read", "-p"]), ["Read"])
        self.assertEqual(agents.claude_tools(["claude", "--tools", "", "-p"]), [])  # an empty value means no tools
        self.assertIsNone(agents.claude_tools(["claude", "-p"]))

    def test_tool_specifiers_keep_their_spaces(self):
        self.assertEqual(agents.split_tools(["Bash(git log *),Read", "Grep Glob"]), ["Bash(git log *)", "Read", "Grep", "Glob"])
        self.assertEqual(agents.base_name("Bash(git log *)"), "bash")

    def test_aliases_and_equals_forms(self):
        flags = agents.parse_flags(["claude", "--allowed-tools=A", "--disallowed-tools", "B", "C", "--permission-mode=dontAsk"])
        self.assertEqual(flags, {"--allowedTools": ["A"], "--disallowedTools": ["B", "C"], "--permission-mode": ["dontAsk"]})

    def test_server_wide_rules(self):
        for tool in ("mcp__x", "mcp__x__*", "mcp__x__", "mcp__*"):
            self.assertTrue(agents.is_server_wide(tool), tool)
        for tool in ("mcp__x__query", "mcp__my_server__q", "Read"):
            self.assertFalse(agents.is_server_wide(tool), tool)


class RelateGuardTests(unittest.TestCase):
    def problem(self, extra=(), base=RELATE):
        return agents.relate_network_problem(list(base) + list(extra))

    def test_the_preset_shape_is_clean(self):
        self.assertEqual(agents.relate_network_problem(RELATE), "")
        self.assertEqual(agents.relate_network_problem(agents.claude_relate_command(["mcp__g__q"], "/m.json")), "")

    def test_variadic_tools_cannot_hide_a_second_value(self):
        argv = ["claude", "-p", "--tools", "Read", "Bash", "--strict-mcp-config"] + ISO
        self.assertIn("Bash", agents.relate_network_problem(argv))
        argv = ["claude", "-p", "--tools", "Read", "WebFetch", "--strict-mcp-config"] + ISO
        self.assertIn("WebFetch", agents.relate_network_problem(argv))

    def test_tools_default_gives_everything_and_is_refused(self):
        self.assertIn("default", agents.relate_network_problem(["claude", "-p", "--tools", "default", "--strict-mcp-config"]))
        self.assertIn("default", agents.relate_network_problem(["claude", "-p", "--tools=default"]))
        self.assertIn("default", agents.read_private_problem(["claude", "-p", "--tools", "default", "--strict-mcp-config"] + ISO))

    def test_unknown_tool_names_are_refused_unless_allow_listed_for_the_pass(self):
        for tool in ("PowerShell", "Agent", "Task", "Edit", "Write", "NotebookEdit", "TodoWrite", "Monitor"):
            argv = ["claude", "-p", "--tools", f"Read,{tool}", "--strict-mcp-config"] + ISO
            self.assertIn(tool, agents.relate_network_problem(argv), tool)
        ok = ["claude", "-p", "--tools", "Read,Grep,Glob", "--strict-mcp-config"] + ISO
        self.assertEqual(agents.relate_network_problem(ok), "")
        # pass-specific: Read is fine in relate but not in read, and WebFetch the other way round
        self.assertIn("Read", agents.read_private_problem(["claude", "-p", "--tools", "WebFetch,Read", "--strict-mcp-config"] + ISO))
        self.assertIn("WebFetch", agents.relate_network_problem(["claude", "-p", "--tools", "WebFetch", "--strict-mcp-config"] + ISO))

    def test_allowed_tools_entries_are_checked_too(self):
        for rule in ("Bash(git log *)", "PowerShell", "Agent", "WebFetch(domain:x)"):
            self.assertNotEqual(self.problem(["--allowedTools", rule]), "", rule)
        self.assertEqual(self.problem(["--allowedTools", "Read,Grep"]), "")

    def test_flags_that_bypass_or_widen_permissions_are_refused(self):
        cases = (["--dangerously-skip-permissions"], ["--allow-dangerously-skip-permissions"],
                 ["--settings", "x.json"], ["--settings={}"], ["--plugin-dir", "/p"], ["--plugin-url", "https://x/p.zip"],
                 ["--permission-mode", "bypassPermissions"], ["--permission-mode=bypassPermissions"])
        for extra in cases:
            self.assertNotEqual(self.problem(extra), "", extra)
            self.assertNotEqual(agents.read_private_problem(READ + extra), "", extra)

    def test_a_server_wide_allow_rule_is_refused(self):
        base = RELATE + ["--mcp-config", "m.json", "--permission-mode", "dontAsk"]
        for rule in ("mcp__x", "mcp__x__*"):
            self.assertIn("whole MCP server", agents.relate_network_problem(base + ["--allowedTools", rule]))
        self.assertEqual(agents.relate_network_problem(base + ["--allowedTools", "mcp__x__q"]), "")

    def test_disallowing_a_tool_is_not_a_false_positive(self):
        self.assertEqual(self.problem(["--disallowedTools", "WebFetch"]), "")
        self.assertEqual(self.problem(["--disallowed-tools", "WebFetch,WebSearch,Bash"]), "")
        self.assertEqual(agents.read_private_problem(READ + ["--disallowedTools", "Bash", "Read"]), "")

    def test_other_text_mentioning_a_tool_is_not_a_false_positive(self):
        self.assertEqual(self.problem(["--append-system-prompt", "never use WebFetch"]), "")

    def test_read_command_without_isolation_loads_claude_md_and_is_refused(self):
        bare = ["claude", "-p", "--tools", "WebFetch,WebSearch", "--strict-mcp-config"]
        self.assertIn("CLAUDE.md", agents.read_private_problem(bare))
        self.assertEqual(agents.read_private_problem(READ), "")
        self.assertEqual(agents.read_private_problem(bare + ["--safe-mode"]), "")

    def test_isolation_detection(self):
        self.assertTrue(agents.has_isolation(["claude", "--setting-sources", ""]))
        self.assertTrue(agents.has_isolation(["claude", "--setting-sources="]))
        self.assertTrue(agents.has_isolation(["claude", "--safe-mode"]))
        self.assertFalse(agents.has_isolation(["claude", "--setting-sources", "user"]))
        self.assertFalse(agents.has_isolation(["claude", "--setting-sources", "user,project"]))
        self.assertFalse(agents.has_isolation(["claude", "-p"]))


class StrictGuardTests(unittest.TestCase):
    """The custom-command guard is an allow-list of flags, and reads argv the way claude does."""

    def test_unknown_flags_are_refused_in_both_passes(self):
        for flag in ("--some-new-flag", "--worktree", "--cloud", "--remote-control", "--tmux", "-x"):
            self.assertIn(flag, agents.relate_network_problem(RELATE + [flag]), flag)
            self.assertIn(flag, agents.read_private_problem(READ + [flag]), flag)

    def test_an_unknown_flag_takes_the_next_token_as_its_value(self):
        # --new-flag swallows "--setting-sources", so isolation is missing as well as the flag being unknown
        argv = ["claude", "-p", "--tools", "WebFetch", "--strict-mcp-config", "--new-flag", "--setting-sources", ""]
        self.assertEqual(agents.parse_flags(argv)["--new-flag"], ["--setting-sources"])
        self.assertNotIn("--setting-sources", agents.parse_flags(argv))
        self.assertFalse(agents.has_isolation(argv))
        self.assertIn("--new-flag", agents.read_private_problem(argv))

    def test_known_flags_the_presets_and_sensible_commands_use_are_accepted(self):
        extra = ["--model", "sonnet", "--effort", "high", "--append-system-prompt", "be brief", "--no-chrome",
                 "--max-budget-usd", "1", "--json-schema", "{}", "--no-session-persistence", "--verbose"]
        self.assertEqual(agents.relate_network_problem(RELATE + extra), "")
        self.assertEqual(agents.read_private_problem(READ + extra), "")
        for preset in (agents.claude_read_command(), agents.claude_relate_command(["mcp__g__q"], "/m.json", ["/d"])):
            self.assertEqual(agents.unknown_flags(preset, "relate"), [], preset)
        self.assertEqual(agents.read_private_problem(agents.claude_read_command()), "")
        self.assertEqual(agents.relate_network_problem(agents.claude_relate_command()), "")

    def test_a_value_option_swallows_the_next_token_even_when_it_starts_with_a_dash(self):
        argv = ["claude", "-p", "--append-system-prompt", "--setting-sources", "--tools", "WebFetch",
                "--strict-mcp-config", "--disable-slash-commands"]
        self.assertEqual(agents.parse_flags(argv)["--append-system-prompt"], ["--setting-sources"])
        # with the empty value after it, the "" is a stray operand: refused as well
        self.assertIn("extra argument", agents.read_private_problem(argv[:3] + ["--setting-sources", ""] + argv[4:]))
        self.assertNotIn("--setting-sources", agents.parse_flags(argv))
        self.assertFalse(agents.has_isolation(argv))
        self.assertIn("CLAUDE.md", agents.read_private_problem(argv))
        # the same swallowing for --model, --permission-mode and a bare --safe-mode text
        self.assertFalse(agents.has_isolation(["claude", "--model", "--safe-mode"]))
        self.assertFalse(agents.has_isolation(["claude", "--system-prompt", "--bare"]))
        # and with the value in place, the flag counts
        self.assertTrue(agents.has_isolation(["claude", "--append-system-prompt", "x", "--setting-sources", ""]))

    def test_a_permission_mode_value_that_looks_like_a_flag_is_not_a_hidden_flag(self):
        flags = agents.parse_flags(["claude", "--permission-mode", "--strict-mcp-config", "dontAsk"])
        self.assertEqual(flags["--permission-mode"], ["--strict-mcp-config"])
        self.assertNotIn("--strict-mcp-config", flags)

    def test_double_dash_ends_the_options(self):
        flags = agents.parse_flags(["claude", "-p", "--", "--setting-sources", ""])
        self.assertNotIn("--setting-sources", flags)

    def test_variadic_first_value_is_taken_then_stops_at_the_next_option(self):
        self.assertEqual(agents.parse_flags(["claude", "--tools", "--strict-mcp-config", "Read", "-p"])["--tools"],
                         ["--strict-mcp-config", "Read"])

    def test_every_forbidden_flag_is_refused_by_name(self):
        for flag in ("--chrome", "--plugin-dir-no-mcp", "--managed-settings", "--inherit-permission-mode", "--agent",
                     "--agents", "--channels", "--ide", "--sdk-url", "--settings", "--plugin-dir", "--plugin-url",
                     "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions"):
            for extra in ([flag], [flag, "x"], [f"{flag}=x"]):
                for problem in (agents.relate_network_problem(RELATE + extra), agents.read_private_problem(READ + extra)):
                    self.assertIn(flag, problem, extra)
                    self.assertNotIn("does not know", problem, extra)  # named, not merely unknown

    def test_read_pass_refuses_every_mcp_entry(self):
        for tool in ("mcp__x__q", "mcp__x", "mcp__x__*"):
            for flag in ("--tools", "--allowedTools"):
                argv = ["claude", "-p", flag, tool, "--tools", "WebFetch", "--strict-mcp-config"] + ISO
                self.assertIn("MCP", agents.read_private_problem(argv), (flag, tool))
        self.assertIn("--add-dir", agents.read_private_problem(READ + ["--add-dir", "/notes"]))
        # relate still accepts a single named tool
        self.assertEqual(agents.relate_network_problem(RELATE + ["--allowedTools", "mcp__x__q"]), "")

    def test_relate_server_wide_rule_is_refused_even_without_mcp_config(self):
        for rule in ("mcp__x", "mcp__x__*", "mcp__*"):
            self.assertIn("whole MCP server", agents.relate_network_problem(RELATE + ["--allowedTools", rule]), rule)
            self.assertIn("whole MCP server", agents.relate_network_problem(RELATE + ["--tools", "Read", rule]), rule)
        self.assertEqual(agents.relate_network_problem(RELATE + ["--allowedTools", "mcp__x__q"]), "")


class StrictGuardPlanTests(helpers.TempCase):
    def test_unknown_flags_refuse_unless_the_pass_override_is_set(self):
        read, relate = READ + ["--brand-new", "x"], RELATE + ["--brand-new", "x"]
        with self.assertRaises(enrich.EnrichmentError) as ctx:
            enrich.command_plan(self.config(enrich={"read_command": READ, "relate_command": relate}))
        self.assertIn("--brand-new", str(ctx.exception))
        with self.assertRaises(enrich.EnrichmentError) as ctx:
            enrich.command_plan(self.config(enrich={"read_command": read, "relate_command": RELATE}))
        self.assertIn("--brand-new", str(ctx.exception))
        plan = enrich.command_plan(self.config(enrich={"read_command": read, "relate_command": relate,
                                                       "allow_private_in_read": True,
                                                       "allow_network_in_relate": True}))
        self.assertTrue(any("allow_private_in_read is on" in w for w in plan["warnings"]))
        self.assertTrue(any("allow_network_in_relate is on" in w for w in plan["warnings"]))


class SplitterParityTests(unittest.TestCase):
    """split_tools is a port of claude 2.1.303's splitter; nested or unbalanced parentheses are refused."""

    def test_split_matches_claudes_single_flag_paren_reading(self):
        # the first ")" clears the "inside" flag, so the rest of this string splits on spaces again
        self.assertEqual(agents.split_tools(["WebFetch WebSearch(a(b) Read x)"]),
                         ["WebFetch", "WebSearch(a(b)", "Read", "x)"])
        self.assertEqual(agents.split_tools(["Read Grep(a(b) Bash(curl *) x)"]),
                         ["Read", "Grep(a(b)", "Bash(curl *)", "x)"])
        self.assertEqual(agents.split_tools(["Bash(git log *),Read", "Grep Glob"]), ["Bash(git log *)", "Read", "Grep", "Glob"])
        # only "," and " " separate; leading whitespace is skipped; empty values are ignored
        self.assertEqual(agents.split_tools(["  Read\tGrep", "", "\nGlob"]), ["Read\tGrep", "Glob"])
        self.assertEqual(agents.split_tools(["Bash(a, b) , Read"]), ["Bash(a, b)", "Read"])

    def test_nested_and_unbalanced_parentheses_are_refused_in_every_list(self):
        bad = ["WebSearch(a(b) Read", "Read Grep(a(b) Bash(curl *) x)", "Read)", "Read(", "Read(a)(b", "Grep(x(y))"]
        for value in bad:
            self.assertNotEqual(agents.paren_problem([value]), "", value)
            for flag in ("--tools", "--allowedTools", "--disallowedTools"):
                argv = ["claude", "-p", "--tools", "Read", flag, value, "--strict-mcp-config"] + ISO
                self.assertIn("parenthesis", agents.relate_network_problem(argv), (flag, value))
        self.assertEqual(agents.paren_problem(["Bash(git log *) Read(a b)", "Grep"]), "")

    def test_the_reported_bypass_strings_are_refused(self):
        tools = "WebFetch WebSearch(a(b) Read x)"
        allowed = "WebFetch WebSearch(a(b) Read(//**) x)"
        read = ["claude", "-p", "--output-format", "json", "--tools", tools, "--allowedTools", allowed,
                "--permission-mode", "dontAsk", "--strict-mcp-config"] + ISO
        self.assertNotEqual(agents.read_private_problem(read), "")
        relate = ["claude", "-p", "--tools", "Read Grep(a(b) Bash x)", "--allowedTools", "Read Grep(a(b) Bash(curl *) x)",
                  "--permission-mode", "dontAsk", "--strict-mcp-config"] + ISO
        self.assertNotEqual(agents.relate_network_problem(relate), "")
        # a server-wide rule hidden behind the same trick is not missed either
        hidden = ["claude", "-p", "--tools", "Read", "--allowedTools", "Grep(a(b) mcp__x x)", "--strict-mcp-config"] + ISO
        self.assertNotEqual(agents.relate_network_problem(hidden), "")

    def test_a_tools_entry_with_a_specifier_is_refused(self):
        for tools in ("Read(//**)", "WebFetch(domain:x)"):
            argv = ["claude", "-p", "--tools", tools, "--strict-mcp-config"] + ISO
            self.assertIn("specifier", agents.relate_network_problem(argv) + agents.read_private_problem(argv), tools)
        ok = ["claude", "-p", "--tools", "Read,Grep", "--allowedTools", "Bash(git log *)", "--strict-mcp-config"] + ISO
        self.assertNotIn("specifier", agents.relate_network_problem(ok))  # (Bash is refused for another reason)


class OperandAndFlagScopeTests(unittest.TestCase):
    def test_positional_tokens_are_refused_including_after_double_dash(self):
        for extra in (["rc"], ["mcp", "serve"], ["plugin", "install", "x@y"], ["--", "mcp", "serve"], ["--", "--tools", "Bash"],
                      ["prompt text"], ["-"]):
            self.assertIn("extra argument", agents.read_private_problem(READ + extra), extra)
            self.assertIn("extra argument", agents.relate_network_problem(RELATE + extra), extra)
        self.assertEqual(agents.positional_args(agents.claude_read_command()), [])
        self.assertEqual(agents.positional_args(agents.claude_relate_command(["mcp__g__q"], "/m.json", ["/d"])), [])
        # a value that merely looks like an operand is a value, not an operand
        self.assertEqual(agents.positional_args(["claude", "--model", "rc", "--permission-mode", "dontAsk"]), [])

    def test_system_prompt_file_flags_are_relate_only(self):
        for flag in ("--system-prompt-file", "--append-system-prompt-file", "--add-dir", "--mcp-config"):
            problem = agents.read_private_problem(READ + [flag, "/p.txt"])
            self.assertIn(flag, problem)
            self.assertIn("only allowed in the relate pass", problem)
            self.assertNotIn("does not know", problem)
        for flag in ("--system-prompt-file", "--append-system-prompt-file"):
            self.assertEqual(agents.relate_network_problem(RELATE + [flag, "/p.txt"]), "")
        self.assertEqual(agents.read_private_problem(READ + ["--system-prompt", "inline text"]), "")

    def test_inherit_permission_mode_takes_a_value(self):
        flags = agents.parse_flags(["claude", "--inherit-permission-mode", "--setting-sources", ""])
        self.assertEqual(flags["--inherit-permission-mode"], ["--setting-sources"])
        self.assertIn("--inherit-permission-mode", agents.read_private_problem(READ + ["--inherit-permission-mode"]))


class FinalArgvTests(helpers.TempCase):
    def test_the_command_with_the_context_folders_appended_is_checked_again(self):
        folder = self.tmp / "notes"
        folder.mkdir()
        # claude takes "--add-dir" as the VALUE of the dangling --append-system-prompt, leaving the folder an operand
        relate = RELATE + ["--append-system-prompt"]
        plan = {"relate": relate, "relate_kind": "custom", "mcp": enrich.mcp.McpSelection()}
        with self.assertRaises(enrich.EnrichmentError) as ctx:
            enrich.relate_argv(plan, [("notes", folder)], None)
        self.assertIn("extra argument", str(ctx.exception))
        self.assertEqual(enrich.relate_argv({**plan, "allow_network_in_relate": True}, [("notes", folder)], None)[-3:],
                         ["--append-system-prompt", "--add-dir", str(folder)])
        ok = {"relate": RELATE, "relate_kind": "custom", "mcp": enrich.mcp.McpSelection()}
        self.assertEqual(enrich.relate_argv(ok, [("notes", folder)], None)[-2:], ["--add-dir", str(folder)])


class PlanWarningTests(helpers.TempCase):
    def plan(self, read, relate, **over):
        return enrich.command_plan(self.config(enrich={"read_command": read, "relate_command": relate, **over}), strict=False)

    def test_wrappers_get_a_loud_cannot_verify_warning(self):
        for argv in (["env", "claude", "-p", "--tools", "Read"], ["npx", "@anthropic-ai/claude-code", "-p"],
                     ["/usr/local/bin/my-launcher", "--model", "x"], ["bash", "-c", "claude -p"]):
            plan = self.plan(READ, argv)
            hits = [w for w in plan["warnings"] if "cannot verify isolation" in w and "relate" in w]
            self.assertEqual(len(hits), 1, argv)
        wrapper = self.plan(READ, ["env", "claude", "-p"])["warnings"]
        self.assertTrue(any("wrapper around Claude Code" in w for w in wrapper))
        launcher = self.plan(READ, ["/usr/local/bin/my-launcher"])["warnings"]
        self.assertFalse(any("wrapper around Claude Code" in w for w in launcher))

    def test_direct_claude_commands_are_read_not_warned_about(self):
        plan = self.plan(READ, RELATE)
        self.assertEqual(plan["warnings"], [])

    def test_a_relate_command_that_loads_user_settings_warns(self):
        bare = ["claude", "-p", "--tools", "Read", "--strict-mcp-config"]
        warnings = self.plan(READ, bare)["warnings"]
        self.assertTrue(any("loads your Claude settings" in w for w in warnings))

    def test_a_relate_command_with_a_blanket_read_rule_warns_but_still_runs(self):
        for rule in ("Read", "Grep,Glob", "Read(*)", "Read(//**)", "Read(~/notes/**)", "Glob(/etc/**)"):
            relate = RELATE + ["--allowedTools", rule]
            self.assertEqual(agents.relate_network_problem(relate), "", rule)  # not a refusal
            warnings = self.plan(READ, relate)["warnings"]
            hits = [w for w in warnings if "any file you can read" in w]
            self.assertEqual(len(hits), 1, rule)
            self.assertIn("context folders", hits[0])
        for rule in ("Read(docs/**)", "mcp__g__query", "Bash(git log *)"):
            self.assertFalse([w for w in self.plan(READ, RELATE + ["--allowedTools", rule])["warnings"]
                              if "any file you can read" in w], rule)
        self.assertEqual(agents.relate_read_warning(["env", "claude", "--allowedTools", "Read"]), "")  # not readable: other warning
        self.assertEqual(agents.relate_read_warning(agents.claude_relate_command(["mcp__g__q"], "/m.json", ["/d"])), "")

    def test_status_shows_the_warning(self):
        env = {"UNHOARD_HOME": str(self.paths.data_dir), "UNHOARD_CONFIG": str(self.paths.config_file),
               "HOME": str(self.tmp)}
        self.paths.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.config_file.write_text(f'[enrich]\nread_command = {json.dumps(READ)}\nrelate_command = ["env", "claude", "-p"]\n')
        out = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(out):
            self.assertEqual(cli.main(["status"]), 0)
        self.assertTrue(any("cannot verify isolation" in w for w in json.loads(out.getvalue())["enrich_warnings"]))

    def test_enrich_prints_the_warning_and_returns_it(self):
        sync.ingest_payload(self.db, helpers.fixture("bookmarks_page1.json"))
        agent = str(Path(__file__).resolve().parent / "fake_agent.py")
        cfg = self.config(enrich={"read_command": [sys.executable, agent, "ok"], "relate_command": [sys.executable, agent, "ok"]})
        out = enrich.enrich_pending(self.db, cfg, limit=1)
        self.assertEqual(len([w for w in out["warnings"] if "cannot verify isolation" in w]), 2)
        env = {"UNHOARD_HOME": str(self.paths.data_dir), "UNHOARD_CONFIG": str(self.paths.config_file),
               "HOME": str(self.tmp)}
        self.paths.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.config_file.write_text(f'[enrich]\nread_command = [{json.dumps(sys.executable)}, {json.dumps(agent)}, "ok"]\n'
                                          f'relate_command = [{json.dumps(sys.executable)}, {json.dumps(agent)}, "ok"]\n')
        err = io.StringIO()
        with mock.patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
            self.assertEqual(cli.main(["enrich", "--limit", "1"]), 0)
        self.assertIn("WARNING: cannot verify isolation", err.getvalue())

    def test_the_old_blunt_text_search_is_gone_so_a_prompt_text_cannot_refuse_a_command(self):
        cfg = self.config(enrich={"read_command": READ, "relate_command": RELATE + ["--append-system-prompt", "WebFetch is off"]})
        enrich.command_plan(cfg)  # does not raise


if __name__ == "__main__":
    unittest.main()
