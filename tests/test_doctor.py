import helpers
import json
import os
import stat
import sys
import unittest
from pathlib import Path

from unhoard import agents, doctor, enrich


def init_event(tools, servers=(), mode="dontAsk"):
    return {"type": "system", "subtype": "init", "tools": list(tools), "permissionMode": mode,
            "mcp_servers": [{"name": s, "status": "connected"} for s in servers],
            "plugins": [{"name": "cc-plugin-telemetry", "path": "builtin", "source": "cc-plugin-telemetry@builtin"}],
            "skills": []}


CLEAN_ANSWER = json.dumps({"user_instructions": "NONE", "hook_context": "NONE"})


def stream(init, answer=CLEAN_ANSWER, hooks=0):
    events = [init] + [{"type": "system", "subtype": "hook_started", "hook_event": "SessionStart"}] * hooks
    events += [{"type": "assistant", "message": {"content": []}}, {"type": "result", "result": answer}]
    return "\n".join(json.dumps(e) for e in events)


def statuses(checks):
    return {c.name: c.status for c in checks}


class ConfigModeTests(unittest.TestCase):
    def check(self, mode, **x):
        import tempfile
        from pathlib import Path
        from unhoard.config import Config, Paths
        with tempfile.TemporaryDirectory() as tmp:
            file = Path(tmp) / "config.toml"
            file.write_text("[x]\n")
            file.chmod(mode)
            return doctor.check_config_mode(Config({"x": x}, Paths(Path(tmp) / "d", file), env={}))

    def test_warns_when_a_secret_is_in_a_group_or_world_readable_config(self):
        for key in ("client_secret", "bearer_token"):
            for mode in (0o644, 0o640, 0o604):
                found = self.check(mode, **{key: "s3cret-value"})
                self.assertEqual(found.status, doctor.WARN, (key, oct(mode)))
                self.assertIn("chmod 600", found.detail)
                self.assertIn(key, found.detail)
                self.assertNotIn("s3cret-value", found.detail)  # names the key, never the value

    def test_no_warning_for_a_private_file_or_when_there_is_no_secret(self):
        self.assertIsNone(self.check(0o600, client_secret="x"))
        self.assertIsNone(self.check(0o644, client_id="only-an-id"))
        self.assertIsNone(self.check(0o644))


class ClaudeCheckTests(unittest.TestCase):
    def test_parse_init_skips_noise(self):
        out = "not json\n" + stream(init_event(["Read"]))
        self.assertEqual(doctor.parse_init(out)["tools"], ["Read"])
        self.assertIsNone(doctor.parse_init("nothing here\n{}"))

    def test_stream_argv(self):
        argv = doctor.stream_argv(agents.claude_read_command())
        self.assertEqual(argv[argv.index("--output-format") + 1], "stream-json")
        self.assertEqual(argv[-2:], ["--include-hook-events", "--verbose"])  # claude 2.1.303 lists this flag in --help

    def test_quotes_are_compared_with_the_home_folder_instruction_files_too(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            (home / "CLAUDE.md").write_text("# Notes\nAlways answer in haiku about tide pools.\n")
            (home / "AGENTS.md").write_text("Never touch the vintage synthesizer collection.\n")
            (home / ".claude").mkdir()
            (home / ".claude" / "CLAUDE.md").write_text("Prefer tabs.\n")
            text = doctor.local_instruction_text({}, home)
        for line in ("always answer in haiku about tide pools", "never touch the vintage synthesizer collection", "prefer tabs"):
            self.assertIn(line, text)
        answer = json.dumps({"user_instructions": "Never touch the vintage synthesizer collection.", "hook_context": "NONE"})
        checks = doctor.check_isolation("read", init_event(["WebFetch"]), 0, answer, text)
        self.assertEqual(statuses(checks)["read pass: no CLAUDE.md or other instruction text in its context"], "fail")

    def test_read_pass_passes_and_fails(self):
        good = doctor.check_claude_read(init_event(["WebFetch", "WebSearch"]))
        self.assertEqual({c.status for c in good}, {"pass"})
        for tools, servers in ((["WebFetch", "Read"], ()), (["WebFetch", "Bash"], ()),
                               (["WebFetch", "mcp__x__y"], ("x",)), (["WebFetch"], ("x",))):
            bad = doctor.check_claude_read(init_event(tools, servers))
            self.assertIn("fail", {c.status for c in bad}, (tools, servers))

    def test_relate_pass_passes_with_only_enabled_mcp(self):
        init = init_event(["Read", "Grep", "Glob", "mcp__gbrain__query", "mcp__gbrain__put_page"], ["gbrain"])
        self.assertEqual({c.status for c in doctor.check_claude_relate(init, ["gbrain"])}, {"pass"})
        plain = init_event(["Read", "Grep", "Glob"])
        self.assertEqual({c.status for c in doctor.check_claude_relate(plain, [])}, {"pass"})

    def test_relate_pass_fails_on_web_bash_other_builtins_and_stray_mcp(self):
        cases = [
            (init_event(["Read", "WebFetch"]), []),
            (init_event(["Read", "Bash"]), []),
            (init_event(["Read", "Write"]), []),
            (init_event(["Read", "mcp__exa__search"], ["exa"]), ["gbrain"]),
            (init_event(["Read", "mcp__exa__search"]), []),
            (init_event(["Read"], ["exa"]), []),
            (init_event(["Read", "mcp__gbrain__query"], ["gbrain"], mode="default"), ["gbrain"]),
        ]
        for init, enabled in cases:
            self.assertIn("fail", {c.status for c in doctor.check_claude_relate(init, enabled)}, init)


class ChromeCheckTests(unittest.TestCase):
    def test_chrome_and_claudeai_connectors_fail_the_check(self):
        self.assertEqual(doctor.check_chrome_off("read", init_event(["WebFetch"])).status, "pass")
        for tools, servers in ((["mcp__claude-in-chrome__navigate"], ()), (["Read"], ("claude-in-chrome",)),
                               (["mcp__claude_ai_Gmail__search"], ()), (["Read"], ("claude.ai Gmail",))):
            check = doctor.check_chrome_off("relate", init_event(tools, servers))
            self.assertEqual(check.status, "fail", (tools, servers))
            self.assertIn("loaded:", check.detail)


class RunDoctorTests(helpers.TempCase):
    def run_with(self, cfg, responder):
        calls = []

        def runner(argv, prompt, timeout, cwd):
            calls.append({"argv": argv, "prompt": prompt, "cwd": cwd})
            if "--mcp-config" in argv:
                path = argv[argv.index("--mcp-config") + 1]
                calls[-1]["mcp_path"] = path
                calls[-1]["mcp_mode"] = stat.S_IMODE(os.stat(path).st_mode)
            return responder(argv, prompt, calls[-1])
        with helpers.fake_home(self.tmp / "home", helpers.CLAUDE_SERVERS):
            checks = doctor.run_doctor(cfg, runner)
        return checks, calls

    def claude_responder(self, relate_tools=("Read", "Grep", "Glob"), servers=(), mode="dontAsk"):
        def respond(argv, prompt, call):
            if "WebFetch,WebSearch" in argv:
                return 0, stream(init_event(["WebFetch", "WebSearch"])), ""
            return 0, stream(init_event(relate_tools, servers, mode)), ""
        return respond

    def test_claude_preset_all_pass(self):
        cfg = self.config(enrich={"agent": "claude"}, context={"mcp_servers": ["gbrain"]})
        tools = ["Read", "Grep", "Glob", "mcp__gbrain__query", "mcp__gbrain__put_page"]
        checks, calls = self.run_with(cfg, self.claude_responder(tools, ["gbrain"]))
        self.assertEqual({c.status for c in checks}, {"pass"}, doctor.render(checks))
        self.assertEqual(len(calls), 2)
        for call in calls:
            self.assertIn("stream-json", call["argv"])
            self.assertEqual(call["cwd"], str(self.paths.scratch_dir))
        self.assertIn("read pass: Claude in Chrome and claude.ai connectors off", statuses(checks))
        self.assertIn("relate pass: Claude in Chrome and claude.ai connectors off", statuses(checks))
        for call in calls:
            self.assertIn("--no-chrome", call["argv"])
        self.assertEqual(calls[1]["mcp_mode"], 0o600)
        self.assertFalse(os.path.exists(calls[1]["mcp_path"]))  # generated file removed
        self.assertNotIn(helpers.SECRET, doctor.render(checks))

    def test_claude_failure_is_reported_and_renders_a_table(self):
        cfg = self.config(enrich={"agent": "claude"})
        checks, _ = self.run_with(cfg, self.claude_responder(("Read", "WebSearch", "Grep", "Glob")))
        self.assertEqual(statuses(checks)["relate pass: no web or Bash tools"], "fail")
        table = doctor.render(checks)
        self.assertIn("FAIL", table)
        self.assertIn("relate pass: no web or Bash tools", table)

    def test_agent_that_does_not_start_is_a_failure(self):
        cfg = self.config(enrich={"agent": "claude"})
        checks, _ = self.run_with(cfg, lambda *a: (127, "", "claude not found"))
        self.assertEqual([c.status for c in checks], ["fail", "fail"])
        self.assertIn("claude not found", checks[0].detail)

    def test_custom_commands_are_skipped(self):
        cfg = self.config(enrich={"read_command": ["my-agent"], "relate_command": ["my-agent"]})
        checks, calls = self.run_with(cfg, lambda *a: (0, "", ""))
        self.assertEqual(calls, [])
        self.assertEqual([c.status for c in checks if c.name.endswith("pass")], ["skip", "skip"])
        # and each unreadable command is called out loudly
        self.assertEqual(sum("cannot verify isolation" in c.detail for c in checks if c.name == "configuration"), 2)

    def test_hooks_that_ran_are_a_failure(self):
        cfg = self.config(enrich={"agent": "claude"})
        responder = lambda argv, prompt, call: (0, stream(init_event(["WebFetch", "WebSearch"] if "WebFetch,WebSearch" in argv
                                                                  else ["Read", "Grep", "Glob"]), hooks=2), "")
        checks, _ = self.run_with(cfg, responder)
        self.assertEqual(statuses(checks)["read pass: no hooks ran"], "fail")
        self.assertEqual(statuses(checks)["relate pass: no hooks ran"], "fail")

    def test_a_user_plugin_or_skill_is_flagged(self):
        init = init_event(["WebFetch", "WebSearch"])
        init["plugins"].append({"name": "my-private-plugin", "path": "/Users/me/.claude/plugins/x", "source": "x@inline"})
        init["skills"] = ["my-skill"]
        checks = doctor.check_isolation("read", init, 0, CLEAN_ANSWER, "")
        by = statuses(checks)
        self.assertEqual(by["read pass: no user plugins"], "fail")
        self.assertEqual(by["read pass: no skills or slash commands"], "warn")

    def test_canary_quote_of_the_users_own_claude_md_fails(self):
        cfg = self.config(enrich={"agent": "claude"})
        home = self.tmp / "home"
        home.mkdir()
        (home / ".claude").mkdir()
        (home / ".claude" / "CLAUDE.md").write_text("# Team shared-policy reference\n\nSecret rules.\n")
        leaked = json.dumps({"user_instructions": "# Team shared-policy reference", "hook_context": "NONE"})
        responder = lambda argv, prompt, call: (0, stream(init_event(["WebFetch", "WebSearch"] if "WebFetch,WebSearch" in argv
                                                                  else ["Read", "Grep", "Glob"]), answer=leaked), "")
        checks, calls = self.run_with(cfg, responder)
        by = statuses(checks)
        self.assertEqual(by["read pass: no CLAUDE.md or other instruction text in its context"], "fail")
        self.assertEqual(by["relate pass: no CLAUDE.md or other instruction text in its context"], "fail")
        # the canary prompt never contains the user's text: the agent is asked to quote, the doctor compares locally
        self.assertTrue(all("Team shared" not in c["prompt"] for c in calls))

    def test_a_quote_that_matches_nothing_local_is_only_a_warning(self):
        checks = doctor.check_isolation("read", init_event(["WebFetch"]), 0,
                                        json.dumps({"user_instructions": "You are an interactive agent", "hook_context": "NONE"}),
                                        "# my notes")
        self.assertIn("warn", {c.status for c in checks})
        self.assertNotIn("fail", {c.status for c in checks})

    def test_effective_tools_catch_a_server_wide_allow_rule(self):
        init = init_event(["Read", "Grep", "Glob", "mcp__gbrain__query", "mcp__gbrain__put_page"], ["gbrain"])
        wide = ["claude", "-p", "--allowedTools", "mcp__gbrain", "--permission-mode", "dontAsk"]
        self.assertEqual(sorted(doctor.effective_mcp_tools(init, wide)), ["mcp__gbrain__put_page", "mcp__gbrain__query"])
        checks = doctor.check_claude_relate(init, ["gbrain"], wide, ["mcp__gbrain__query"])
        self.assertEqual(statuses(checks)["relate pass: effective MCP tools are only the selected read-only tools"], "fail")
        star = ["claude", "-p", "--allowedTools", "mcp__gbrain__*"]
        self.assertEqual(len(doctor.effective_mcp_tools(init, star)), 2)
        exact = ["claude", "-p", "--allowedTools", "mcp__gbrain__query", "--disallowedTools", "mcp__gbrain__put_page"]
        self.assertEqual(doctor.effective_mcp_tools(init, exact), ["mcp__gbrain__query"])
        denied = ["claude", "-p", "--allowedTools", "mcp__gbrain", "--disallowedTools", "mcp__gbrain__put_page"]
        self.assertEqual(doctor.effective_mcp_tools(init, denied), ["mcp__gbrain__query"])

    def test_doctor_remembers_exposed_tool_names_for_disallowed_tools(self):
        cfg = self.config(enrich={"agent": "claude"}, context={"mcp_servers": ["gbrain"]})
        tools = ["Read", "Grep", "Glob", "mcp__gbrain__query", "mcp__gbrain__put_page"]
        self.run_with(cfg, self.claude_responder(tools, ["gbrain"]))
        from unhoard import mcp
        self.assertEqual(mcp.load_exposed(self.paths.data_dir), {"gbrain": ["mcp__gbrain__query", "mcp__gbrain__put_page"]})
        # the next run (doctor or enrich) denies put_page explicitly
        checks, calls = self.run_with(cfg, self.claude_responder(tools, ["gbrain"]))
        argv = calls[1]["argv"]
        self.assertIn("mcp__gbrain__put_page", argv[argv.index("--disallowedTools") + 1])
        self.assertNotIn("mcp__gbrain__query", argv[argv.index("--disallowedTools") + 1])
        self.assertEqual(stat.S_IMODE(os.stat(mcp.exposed_cache_path(self.paths.data_dir)).st_mode), 0o600)

    TOOLS_OK = ["functions.exec", "tools.apply_patch", "tools.clock__curr_time", "collaboration.spawn_agent"]

    @staticmethod
    def codex_out(tools=None, sandbox=None):
        events = [{"type": "thread.started"}]
        if tools is not None:
            events.append({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps({"tools": tools})}})
        if sandbox is not None:
            events.append({"type": "item.completed", "item": {"type": "command_execution", **sandbox}})
        return "\n".join(json.dumps(e) for e in events)

    def codex_responder(self, tools, sandbox_ok=True):
        def respond(argv, prompt, call):
            self.assertEqual(argv[-2:], ["--json", "-"])
            if "Run exactly this shell command" in prompt:
                token = prompt.split("cat ", 1)[1].strip()
                content = Path(token).read_text()
                if sandbox_ok:
                    return 0, self.codex_out(sandbox={"exit_code": 0, "aggregated_output": content}), ""
                return 0, self.codex_out(sandbox={"exit_code": 1, "aggregated_output": "bwrap: No permissions to create a new namespace"}), ""
            return 0, self.codex_out(tools=tools), ""
        return respond

    def test_codex_checks_are_advisory_and_sandbox_is_tested(self):
        cfg = self.config(enrich={"agent": "codex"})
        checks, calls = self.run_with(cfg, self.codex_responder(self.TOOLS_OK))
        self.assertEqual(len(calls), 3)
        self.assertEqual({c.status for c in checks}, {"pass"}, doctor.render(checks))
        self.assertTrue(all("advisory" in c.name for c in checks if "tool list" in c.name))

    def test_codex_web_tool_is_flagged_and_broken_sandbox_is_reported(self):
        cfg = self.config(enrich={"agent": "codex"})
        checks, _ = self.run_with(cfg, self.codex_responder(self.TOOLS_OK + ["tools.web__run"], sandbox_ok=False))
        by_name = {c.name: c for c in checks}
        self.assertEqual(by_name["relate pass: tool list (advisory, the model reports it)"].status, "warn")
        sandbox = by_name["codex sandbox runs a read-only command"]
        self.assertEqual(sandbox.status, "warn")
        self.assertIn("namespace", sandbox.detail)
        self.assertNotIn("fail", {c.status for c in checks})  # advisory results never fail the run

    def test_cli_doctor_exit_code(self):
        from unhoard import cli
        import contextlib, io
        from unittest import mock
        cfg = self.config(enrich={"agent": "claude"})
        bad = [doctor.Check("x", doctor.FAIL, "no")]
        with mock.patch.object(doctor, "run_doctor", return_value=bad), contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertEqual(cli.cmd_doctor(None, cfg), 1)
        self.assertIn("1 check(s) failed", out.getvalue())


if __name__ == "__main__":
    unittest.main()
