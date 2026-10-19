"""``unhoard doctor``: start each pass with a trivial prompt and check what tools it really has.

For Claude the check is deterministic: ``--output-format stream-json --verbose`` prints a ``system``/``init`` event
that lists the tools and MCP servers the process loaded. For Codex there is no such event, so the model is asked to
list its tools and the result is advisory only. Codex also gets a sandbox check, because ``--sandbox read-only``
needs a working Linux sandbox (bubblewrap with user namespaces) to run any shell command.

A doctor run costs two or three tiny agent calls. It never prints the contents of the generated MCP config.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import agents, enrich, mcp, procs, xauth
from .config import Config
from .launch import resolve_target

PASS, FAIL, WARN, SKIP = "pass", "fail", "warn", "skip"
PROMPT = ('Reply with ONE JSON object and nothing else: {"user_instructions": "<the first line of any '
          'user-level instruction file (CLAUDE.md, AGENTS.md, a memory or notes file) that appears in your context, '
          'copied verbatim, or NONE>", "hook_context": "<the first line of any text that a session-start hook or a '
          'memory feature added to your context, copied verbatim, or NONE>"}. Do not call any tool.')
TOOL_LIST_PROMPT = ('Without calling any tool, list the exact names of every tool or function you can call '
                    '(web, MCP, shell, file, image and others) as a JSON array of strings under the key "tools". '
                    "Reply with that JSON only.")
Runner = Callable[[list[str], str, int, "str | None"], "tuple[int, str, str]"]


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""


def default_runner(argv: list[str], prompt: str, timeout: int, cwd: str | None = None) -> tuple[int, str, str]:
    try:
        proc = procs.run_group(argv, prompt, timeout, cwd, enrich._agent_env(argv))
    except FileNotFoundError:
        return 127, "", f"{argv[0]} not found"
    except subprocess.TimeoutExpired:
        return 124, "", f"timed out after {timeout}s"
    return proc.returncode, proc.stdout, proc.stderr


# --- Claude -----------------------------------------------------------------------------------------------

def stream_argv(argv: list[str]) -> list[str]:
    """The same command, but printing the event stream (needs --verbose with stream-json). ``--include-hook-events``
    makes claude print every hook lifecycle event, so a hook that ran cannot go unnoticed."""
    out = list(argv)
    for i, arg in enumerate(out[:-1]):
        if arg == "--output-format":
            out[i + 1] = "stream-json"
            break
    else:
        out += ["--output-format", "stream-json"]
    return out + ["--include-hook-events", "--verbose"]


def parse_init(stdout: str) -> dict | None:
    """The first ``system``/``init`` event in a stream-json transcript, or None."""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "system" and event.get("subtype") == "init":
            return event
    return None


def parse_stream(stdout: str) -> dict:
    """What the doctor needs from a stream-json run: the init event, how many hooks started, the answer text."""
    out: dict = {"init": None, "hooks": 0, "result": ""}
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("type") == "system" and event.get("subtype") == "init" and out["init"] is None:
            out["init"] = event
        elif event.get("type") == "system" and event.get("subtype") == "hook_started":
            out["hooks"] += 1
        elif event.get("type") == "result" and isinstance(event.get("result"), str):
            out["result"] = event["result"]
    return out


def _server_names(init: dict) -> list[str]:
    return [str(s.get("name")) for s in init.get("mcp_servers") or [] if isinstance(s, dict)]


def _tools(init: dict) -> list[str]:
    return [str(t) for t in init.get("tools") or []]


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[#*`>_-]+", " ", text).lower().split())


def local_instruction_text(env: dict | None = None, home: Path | None = None) -> str:
    """The user's own instruction files (in the Claude and Codex config folders and in the home folder), read locally (never sent anywhere), to recognise a leaked quote."""
    env = os.environ if env is None else env
    home = home or Path.home()
    dirs = [Path(env["CLAUDE_CONFIG_DIR"]).expanduser()] if env.get("CLAUDE_CONFIG_DIR") else []
    # ~/CLAUDE.md and ~/AGENTS.md too: claude and codex read them from the home folder of a session started there
    dirs += [home / ".claude", Path(env.get("CODEX_HOME") or home / ".codex").expanduser(), home]
    parts = []
    for folder in dirs:
        for name in ("CLAUDE.md", "AGENTS.md"):
            try:
                parts.append((folder / name).read_text(encoding="utf-8", errors="replace"))
            except OSError:
                continue
    return _norm("\n".join(parts))


def _quote(answer: str, key: str) -> str:
    """The value of ``key`` in the agent's JSON answer ("" when NONE or unreadable)."""
    start, end = answer.find("{"), answer.rfind("}")
    if start < 0 or end <= start:
        return ""
    try:
        value = json.loads(answer[start:end + 1]).get(key)
    except ValueError:
        return ""
    text = " ".join(str(value or "").split())
    return "" if text.upper().strip(" .\"'") in ("", "NONE", "N/A", "NULL") else text


def check_isolation(pass_name: str, init: dict, hooks: int, answer: str, local_text: str) -> list[Check]:
    """No private context reached the agent: no hooks ran, no plugin or skill from the user, and the agent
    quotes no instruction text (a quote that also appears in your own CLAUDE.md / AGENTS.md is proof)."""
    checks: list[Check] = []
    plugins = [p for p in init.get("plugins") or [] if isinstance(p, dict)
               and not (p.get("path") == "builtin" or str(p.get("source", "")).endswith("@builtin"))]
    skills = [s for s in init.get("skills") or []]
    checks.append(Check(f"{pass_name} pass: no hooks ran", FAIL if hooks else PASS,
                        f"{hooks} hook event(s): a hook can add private text to the context" if hooks else "none"))
    checks.append(Check(f"{pass_name} pass: no user plugins", FAIL if plugins else PASS,
                        "loaded: " + ", ".join(str(p.get("name")) for p in plugins) if plugins else "none"))
    checks.append(Check(f"{pass_name} pass: no skills or slash commands", WARN if skills else PASS,
                        f"{len(skills)} skill(s) loaded" if skills else "none"))
    for key, label in (("user_instructions", "CLAUDE.md or other instruction text"),
                       ("hook_context", "hook or memory text")):
        quoted = _quote(answer, key)
        name = f"{pass_name} pass: no {label} in its context"
        if not quoted:
            checks.append(Check(name, PASS, "the agent quoted nothing" if answer else "no answer to inspect"))
        elif len(_norm(quoted)) >= 6 and _norm(quoted) in local_text:
            checks.append(Check(name, FAIL, f"the agent quoted a line of your own instruction files: {quoted[:80]!r}"))
        else:
            checks.append(Check(name, WARN, f"the agent quoted some text ({quoted[:80]!r}); it does not match your "
                                "instruction files, so it may be invented. Check by hand."))
    return checks


def check_chrome_off(pass_name: str, init: dict) -> Check:
    """Claude in Chrome (a browser with your logged-in sessions) and the connectors of your claude.ai account must
    not be loaded: the presets pass ``--no-chrome`` and run with ``ENABLE_CLAUDEAI_MCP_SERVERS=false``."""
    names = _server_names(init) + _tools(init)
    found = sorted({n for n in names if "chrome" in n.lower() or n.lower().startswith(("claude.ai", "mcp__claude_ai_"))})
    return Check(f"{pass_name} pass: Claude in Chrome and claude.ai connectors off", FAIL if found else PASS,
                 ("loaded: " + ", ".join(found[:6])) if found else
                 "none loaded (--no-chrome, CLAUDE_CODE_ENABLE_CFC=0, ENABLE_CLAUDEAI_MCP_SERVERS=false)")


def check_claude_read(init: dict) -> list[Check]:
    tools = _tools(init)
    builtin = [t for t in tools if not t.startswith("mcp__")]
    extra = sorted(set(builtin) - set(agents.READ_TOOLS))
    mcp_found = _server_names(init) + [t for t in tools if t.startswith("mcp__")]
    return [
        Check("read pass: built-in tools only WebFetch/WebSearch", FAIL if extra else PASS,
              f"unexpected: {', '.join(extra)}" if extra else ", ".join(sorted(builtin)) or "none"),
        Check("read pass: no MCP servers", FAIL if mcp_found else PASS,
              "loaded: " + ", ".join(_server_names(init) or ["(tools named mcp__...)"]) if mcp_found else "none loaded"),
    ]


def effective_mcp_tools(init: dict, argv: list[str]) -> list[str]:
    """The MCP tools the relate agent can really CALL: exposed by a loaded server AND named in ``--allowedTools``
    (minus ``--disallowedTools``). Under ``dontAsk`` with no settings source loaded nothing else grants a tool;
    the isolation checks prove the settings sources are off. This is the effective list, not the selection."""
    flags = agents.parse_flags(argv)
    allowed = agents.split_tools(flags.get("--allowedTools", []))
    denied = {t.lower() for t in agents.split_tools(flags.get("--disallowedTools", []))}
    out = []
    for tool in _tools(init):
        low = tool.lower()
        if not low.startswith("mcp__") or low in denied:
            continue
        for rule in allowed:
            rl = rule.strip().lower()
            if agents.is_server_wide(rl):
                prefix = rl.rstrip("*")
                prefix = prefix if prefix.endswith("__") else prefix + "__"
                hit = low.startswith(prefix)
            else:
                hit = low == rl
            if hit:
                out.append(tool)
                break
    return out


def check_claude_relate(init: dict, enabled: list[str], argv: list[str] | None = None,
                        selected: list[str] | None = None) -> list[Check]:
    tools = _tools(init)
    builtin = [t for t in tools if not t.startswith("mcp__")]
    bad_net = sorted(t for t in builtin if re.match(r"(?i)web|bash", t))
    extra = sorted(set(builtin) - set(agents.RELATE_TOOLS))
    keys = {mcp.server_key(n) for n in enabled}
    servers = _server_names(init)
    stray_servers = sorted(s for s in servers if mcp.server_key(s) not in keys)
    stray_tools = sorted({t for t in tools if t.startswith("mcp__")
                          and t.split("__")[1] not in keys if len(t.split("__")) > 2})
    mode = init.get("permissionMode")
    checks = [
        Check("relate pass: no web or Bash tools", FAIL if bad_net else PASS,
              f"found: {', '.join(bad_net)}" if bad_net else "none"),
        Check("relate pass: built-in tools only Read/Grep/Glob", FAIL if extra else PASS,
              f"unexpected: {', '.join(extra)}" if extra else ", ".join(sorted(builtin)) or "none"),
        Check("relate pass: MCP only from enabled servers", FAIL if stray_servers or stray_tools else PASS,
              ("not enabled: " + ", ".join(stray_servers + stray_tools)) if stray_servers or stray_tools else
              ("servers: " + ", ".join(servers)) if servers else "no MCP servers loaded"),
    ]
    if enabled:
        checks.append(Check("relate pass: permission mode dontAsk (limits MCP to the allowed tools)",
                            PASS if mode == "dontAsk" else FAIL, f"mode is {mode!r}"))
    if argv is not None and selected is not None:
        effective = effective_mcp_tools(init, argv)
        beyond = sorted(set(effective) - set(selected))
        checks.append(Check("relate pass: effective MCP tools are only the selected read-only tools",
                            FAIL if beyond else PASS,
                            ("callable but not selected: " + ", ".join(beyond)) if beyond else
                            f"{len(effective)} callable of {sum(1 for t in tools if t.startswith('mcp__'))} exposed"))
    return checks


def _run_claude(cfg: Config, plan: dict, run: Runner, timeout: int) -> list[Check]:
    checks: list[Check] = []
    scratch = str(cfg.paths.scratch_dir)
    cfg.paths.scratch_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    local = local_instruction_text()
    if plan["read_kind"] == agents.CLAUDE:
        code, out, err = run(stream_argv(plan["read"]), PROMPT, timeout, scratch)
        seen = parse_stream(out)
        init = seen["init"]
        if init is None:
            checks.append(Check("read pass: starts", FAIL, f"no init event (exit {code}): {err.strip()[:200]}"))
        else:
            checks += check_claude_read(init)
            checks += check_isolation("read", init, seen["hooks"], seen["result"], local)
            checks.append(check_chrome_off("read", init))
    if plan["relate_kind"] == agents.CLAUDE:
        selection: mcp.McpSelection = plan["mcp"]
        sources = cfg.context_sources()
        deny = mcp.deny_tools(selection, mcp.load_exposed(cfg.paths.data_dir)) if selection else []
        with mcp.config_file(selection) as path:
            argv = enrich.relate_argv(plan, sources, path, deny)
            code, out, err = run(stream_argv(argv), PROMPT, timeout, scratch)
        seen = parse_stream(out)
        init = seen["init"]
        if init is None:
            checks.append(Check("relate pass: starts", FAIL, f"no init event (exit {code}): {err.strip()[:200]}"))
        else:
            checks += check_claude_relate(init, list(selection.servers), argv, list(selection.tools))
            checks += check_isolation("relate", init, seen["hooks"], seen["result"], local)
            checks.append(check_chrome_off("relate", init))
            exposed = [t for t in _tools(init) if t.startswith("mcp__")]
            if exposed:
                try:
                    mcp.save_exposed(cfg.paths.data_dir, exposed)  # names only; used for --disallowedTools next time
                except OSError:
                    pass
            if selection:
                checks.append(Check("relate pass: MCP tools it may call", PASS,
                                    f"{len(selection.tools)} selected of {len(exposed)} exposed by the servers; "
                                    f"{len(deny)} other tools denied explicitly"))
    return checks


# --- Codex ------------------------------------------------------------------------------------------------

def _json_flag(argv: list[str]) -> list[str]:
    return argv[:-1] + ["--json", argv[-1]] if argv and argv[-1] == "-" else argv + ["--json"]


def codex_events(stdout: str) -> list[dict]:
    events = []
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def codex_tool_names(stdout: str) -> list[str] | None:
    """The tool list the model reported (a JSON object with "tools"), or None."""
    for event in reversed(codex_events(stdout)):
        item = event.get("item") or {}
        if event.get("type") == "item.completed" and item.get("type") == "agent_message":
            text = str(item.get("text") or "")
            start, end = text.find("{"), text.rfind("}")
            if start >= 0 and end > start:
                try:
                    tools = json.loads(text[start:end + 1]).get("tools")
                except ValueError:
                    continue
                if isinstance(tools, list):
                    return [str(t) for t in tools]
    return None


CODEX_FORBIDDEN = {
    "read": r"web|mcp|browser|computer|image_gen|exec_command|write_stdin|view_image",
    "relate": r"web|mcp|browser|computer|image_gen",
}


def check_codex_tools(pass_name: str, stdout: str) -> Check:
    name = f"{pass_name} pass: tool list (advisory, the model reports it)"
    tools = codex_tool_names(stdout)
    if tools is None:
        return Check(name, WARN, "the model did not return a tool list")
    bad = [t for t in tools if re.search(CODEX_FORBIDDEN[pass_name], t, re.I)]
    if bad:
        return Check(name, WARN, "unexpected: " + ", ".join(bad))
    return Check(name, PASS, f"{len(tools)} tools; none for web, MCP or browsers" +
                 ("; no shell" if pass_name == "read" else ""))


def check_codex_sandbox(run: Runner, timeout: int, cfg: Config) -> Check:
    """Run a harmless ``cat`` of a temp file through the relate preset; a sandbox that cannot start fails closed."""
    token = uuid.uuid4().hex
    with tempfile.TemporaryDirectory(prefix="obm-doctor-") as folder:
        target = Path(folder) / "probe.txt"
        target.write_text(token, encoding="utf-8")
        prompt = f"Run exactly this shell command and reply with its output only: cat {target}"
        code, out, err = run(_json_flag(agents.codex_relate_command()), prompt, timeout, folder)
    for event in codex_events(out):
        item = event.get("item") or {}
        if item.get("type") == "command_execution" and event.get("type") == "item.completed":
            output = str(item.get("aggregated_output") or "")
            if item.get("exit_code") == 0 and token in output:
                return Check("codex sandbox runs a read-only command", PASS, "cat of a temp file worked")
            return Check("codex sandbox runs a read-only command", WARN,
                         "the sandbox could not run a command (Codex relate would read nothing): "
                         + " ".join(output.split())[:160])
    return Check("codex sandbox runs a read-only command", WARN,
                 f"no command ran (exit {code}): {(err or out).strip()[:160]}")


def _run_codex(cfg: Config, plan: dict, run: Runner, timeout: int) -> list[Check]:
    checks: list[Check] = []
    scratch = str(cfg.paths.scratch_dir)
    cfg.paths.scratch_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    for name in ("read", "relate"):
        if plan[f"{name}_kind"] == agents.CODEX:
            _, out, _ = run(_json_flag(plan[name]), TOOL_LIST_PROMPT, timeout, scratch)
            checks.append(check_codex_tools(name, out))
    if plan["relate_kind"] == agents.CODEX:
        checks.append(check_codex_sandbox(run, timeout, cfg))
    return checks


def check_launch(cfg: Config) -> Check:
    """What Go will do: the configured adapter and the one that applies now."""
    target = resolve_target(cfg)
    said = target.configured if target.configured == target.effective else f"{target.configured} -> {target.effective}"
    if target.configured == "omnigent" and not str(cfg.get("omnigent", "agent_id", "") or "").strip():
        return Check("launch target", WARN, "adapter omnigent but no [omnigent] agent_id: run `unhoard agents`")
    if target.configured == "auto" and target.omnigent_found and not target.agent_set:
        return Check("launch target", WARN, f"{said}: {target.reason}")
    if target.configured == "auto" and target.agent_set and not target.omnigent_found:
        return Check("launch target", WARN, f"{said}: {target.reason}; Go from the command line fails until it runs, and "
                     "the page shows the brief")
    return Check("launch target", PASS, f"{said}: {target.reason}")


def check_phone(cfg: Config) -> Check | None:
    """A row when ``[server] allowed_hosts`` is set: the page can then be reached by those names, so say what that
    means. Nothing when it is empty (the default: loopback only)."""
    hosts = cfg.allowed_hosts()
    raw = cfg.get("server", "allowed_hosts", [])
    bad = [h for h in raw if isinstance(raw, list) and isinstance(h, str) and h.strip().lower() not in hosts] \
        if isinstance(raw, list) else []
    if not hosts and not raw:
        return None
    if bad or (raw and not hosts):
        return Check("phone access", WARN, "some [server] allowed_hosts entries are invalid and ignored (see "
                     "`unhoard status`, config_problems); valid: " + (", ".join(hosts) or "none"))
    detail = ("the page also answers to " + ", ".join(hosts) + ". Share it only with "
              "`tailscale serve` (your tailnet), never `tailscale funnel` (the public internet); anyone who can "
              "reach it can press Go. The server still listens on 127.0.0.1 only.")
    if cfg.get("server", "require_tailscale_identity", True) is False:
        detail += (" [server] require_tailscale_identity is OFF: requests by that name are no longer required to carry "
                   "Tailscale's identity header, so a mistaken funnel would not be stopped here.")
    return Check("phone access", WARN, detail)


def check_config_mode(cfg: Config) -> Check | None:
    """Warn when the config file holds an X secret and other users can read it. Never reads the secret's value out."""
    holds = [key for key in ("client_secret", "bearer_token") if str(cfg.get("x", key, "") or "").strip()]
    try:
        mode = cfg.paths.config_file.stat().st_mode & 0o777
    except OSError:
        return None
    if not holds or not mode & 0o077:
        return None
    return Check("config file permissions", WARN,
                 f"{cfg.paths.config_file} has mode {mode:03o} and holds [x] {', '.join(holds)}; run: "
                 f"chmod 600 {cfg.paths.config_file}")


# --- entry point ------------------------------------------------------------------------------------------

def run_doctor(cfg: Config, run: Runner | None = None, timeout: int = 180) -> list[Check]:
    run = run or default_runner
    plan = enrich.command_plan(cfg, strict=False)
    checks: list[Check] = []
    token_info = xauth.status_info(cfg)
    if token_info["x_token_source"] or token_info.get("x_login_problem"):  # no token at all: `status` says so
        checks.append(Check("X token", *xauth.describe_source(token_info)))
    phone = check_phone(cfg)
    if phone:
        checks.append(phone)
    loose = check_config_mode(cfg)
    if loose:
        checks.append(loose)
    for message in plan["warnings"]:
        checks.append(Check("configuration", WARN, message))
    for name in ("read", "relate"):
        if plan[f"{name}_kind"] == "custom":
            detail = "custom command: its tools cannot be checked here" if plan[name] else "no command configured"
            checks.append(Check(f"{name} pass", SKIP, detail))
    checks += _run_claude(cfg, plan, run, timeout)
    checks += _run_codex(cfg, plan, run, timeout)
    if plan["relate_kind"] == agents.CLAUDE and plan["mcp"].missing:
        checks.append(Check("relate pass: configured MCP servers", WARN,
                            "not found in your Claude config: " + ", ".join(plan["mcp"].missing)))
    return checks


def render(checks: list[Check]) -> str:
    width = max([len(c.name) for c in checks] + [5])
    rows = [f"{'check'.ljust(width)}  status  detail", f"{'-' * width}  ------  ------"]
    rows += [f"{c.name.ljust(width)}  {c.status.upper().ljust(6)}  {c.detail}" for c in checks]
    return "\n".join(rows)
