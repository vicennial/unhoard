"""The agent commands for the two enrichment passes: presets built at run time, and the guard for custom ones.

``[enrich] agent = "claude" | "codex"`` selects a preset. The argv is built when a pass starts, never written
to the config, so a flag fix in a later release reaches existing users. ``read_command`` / ``relate_command``
stay as custom overrides; a config that still holds one of the argv shapes older releases wrote is treated as
the preset (see ``OLD_PRESETS``).

The rule every command here serves: no agent gets all three of untrusted text, private data and an outbound
channel. Pass 1 (read) sees the post and the web, never your files. Pass 2 (relate) sees your files and tools,
never the post and never the network.
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Mapping, Sequence

from .config import ENV_CLIENT_SECRET

CLAUDE = "claude"
CODEX = "codex"
PRESETS = (CLAUDE, CODEX)

# --- Claude -----------------------------------------------------------------------------------------------

READ_TOOLS = ("WebFetch", "WebSearch")
RELATE_TOOLS = ("Read", "Grep", "Glob")

# Both presets start claude without ANY of the user's customization: no settings files (so no hooks, no
# CLAUDE.md, no permission allow rules, no enabled plugins), no skills/slash commands. ``--setting-sources ""``
# loads no settings source at all; verified live with claude 2.1.303 that this also drops the user's CLAUDE.md,
# SessionStart hooks, installed plugins and permission allow rules, while ``--mcp-config`` servers and
# ``--add-dir`` still work. ``--safe-mode`` was rejected: it also drops ``--mcp-config`` servers. ``--bare`` was
# rejected: it needs an API key and never reads a subscription login.
# ``--no-chrome`` switches the Claude in Chrome integration off: it is a browser the agent could drive (with the
# user's logged-in sessions) and would put a network channel into the relate pass.
ISOLATION_ARGS = ("--setting-sources", "", "--disable-slash-commands", "--no-chrome")

# Environment the Claude presets (and any direct custom `claude` command) run with: plugins named by the
# environment are customization too; auto memory would read a MEMORY.md of the working directory's project;
# CLAUDE_CODE_ENABLE_CFC=0 keeps Claude in Chrome off even if the parent environment turns it on;
# CLAUDE_CODE_DISABLE_CLAUDE_MDS stops CLAUDE.md files loading; ENABLE_CLAUDEAI_MCP_SERVERS=false keeps the
# connectors of the user's claude.ai account out.
CLAUDE_ENV_DROP = ("CLAUDE_CODE_PLUGIN_DIRS",)
CLAUDE_ENV_SET = {"CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1",
                  "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
                  # explicit false (claude reads 0/false/no/off as false and treats 0 as its own default): with
                  # the variable merely unset, a bridge or parent process could still turn Chrome on
                  "CLAUDE_CODE_ENABLE_CFC": "0"}


def claude_read_command() -> list[str]:
    """Pass 1: web read tools only, no files, no MCP servers, no user customization."""
    tools = ",".join(READ_TOOLS)
    return ["claude", "-p", "--output-format", "json", "--tools", tools, "--allowedTools", tools,
            "--permission-mode", "dontAsk", "--strict-mcp-config", *ISOLATION_ARGS]


def claude_relate_command(mcp_tools: Sequence[str] = (), mcp_config: str | None = None,
                          folders: Sequence[Path | str] = (), deny_tools: Sequence[str] = ()) -> list[str]:
    """Pass 2: Read/Grep/Glob inside the context folders plus the allowed read-only MCP tools. No web, no Bash.

    ``--strict-mcp-config`` makes claude load ONLY the servers in ``--mcp-config`` (without it every MCP server
    of the user loads). ``--allowedTools`` lists the MCP tools only: allowing ``Read`` there would let it read
    any path on disk, while under ``dontAsk`` the built-in tools without an allow rule are limited to the
    working directory and the ``--add-dir`` folders (verified with claude 2.1.303). Because no settings source
    is loaded, the user's own allow rules (for example a server-wide ``mcp__server``) cannot widen that list.
    ``deny_tools`` (tools of the enabled servers that are not selected) are denied as well, as a second layer.
    """
    argv = ["claude", "-p", "--output-format", "json", "--tools", ",".join(RELATE_TOOLS)]
    if mcp_tools:
        argv += ["--allowedTools", ",".join(mcp_tools)]
    if deny_tools:
        argv += ["--disallowedTools", ",".join(deny_tools)]
    argv += ["--permission-mode", "dontAsk", "--strict-mcp-config", *ISOLATION_ARGS]
    if mcp_tools and mcp_config:
        argv += ["--mcp-config", str(mcp_config)]
    for folder in folders:
        argv += ["--add-dir", str(folder)]
    return argv


def agent_env(base: Mapping[str, str], argv: Sequence[str], token_name: str) -> dict[str, str]:
    """The environment for an agent program: no X token and no X client secret; for claude also no environment-loaded plugins, no
    Claude in Chrome, no CLAUDE.md files, no claude.ai connectors and no auto memory."""
    env = {k: v for k, v in base.items() if k not in (token_name, ENV_CLIENT_SECRET)}
    if is_claude(argv):
        for name in CLAUDE_ENV_DROP:
            env.pop(name, None)
        env.update(CLAUDE_ENV_SET)
    return env


# --- Codex ------------------------------------------------------------------------------------------------
# `codex exec --sandbox read-only` limits only shell commands. By itself it still exposes a web tool and the
# user's MCP servers (checked with codex-cli 0.160.0), so these flags remove them. `--ignore-rules` also skips
# execpolicy .rules files. Caveat (checked live with 0.160.0): `$CODEX_HOME/AGENTS.md` is still loaded into the
# context even with `--ignore-user-config --ignore-rules`. The Codex read pass has no web and no shell, so that
# text cannot be sent anywhere except to your own model provider.

_CODEX_BASE = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral",
               "--ignore-user-config", "--ignore-rules", "-c", 'web_search="disabled"']
_CODEX_OFF = ("browser_use", "browser_use_external", "computer_use", "apps", "image_generation", "multi_agent")
_CODEX_NO_SHELL = ("shell_tool", "unified_exec", "view_image")


def _codex(features: Sequence[str]) -> list[str]:
    argv = list(_CODEX_BASE)
    for name in features:
        argv += ["--disable", name]
    return argv + ["-"]


def codex_read_command() -> list[str]:
    """Pass 1: no web (a web tool cannot be combined with a proven shell-less tool set), no shell, no MCP."""
    return _codex(_CODEX_OFF + _CODEX_NO_SHELL)


def codex_relate_command() -> list[str]:
    """Pass 2: sandboxed read-only shell (needs a working Linux sandbox), no web, no user MCP servers."""
    return _codex(_CODEX_OFF)


# --- argv shapes older releases wrote into config.toml ---------------------------------------------------

_C = ["claude", "-p", "--output-format", "json", "--tools"]
_OLD_CODEX = ["codex", "exec", "--skip-git-repo-check", "--sandbox", "read-only", "--ephemeral", "-"]
OLD_PRESETS: dict[str, tuple[tuple[str, ...], ...]] = {
    "read": (tuple(_C + ["WebFetch,WebSearch"]), tuple(_C + ["WebFetch,WebSearch", "--strict-mcp-config"]),
             tuple(_OLD_CODEX)),
    "relate": (tuple(_C + ["Read,Grep,Glob"]), tuple(_C + ["Read,Grep,Glob", "--strict-mcp-config"]),
               tuple(_OLD_CODEX)),
}


def old_preset(argv: Sequence[str], pass_name: str) -> str | None:
    """"claude" / "codex" when ``argv`` is exactly an argv an older release wrote for this pass, else None."""
    shape = tuple(argv)
    for old in OLD_PRESETS[pass_name]:
        if shape == old:
            return CODEX if old[0] == "codex" else CLAUDE
    return None




# --- reading a custom claude command (the guard) ---------------------------------------------------------
# Custom commands are checked structurally, not by searching the text for tool names. Only a command whose
# program is `claude` can be read; anything else (`env claude`, `npx @anthropic-ai/claude-code`, a script)
# gets a loud "cannot verify isolation" warning instead (see ``unverified_warning``).

# How claude (a commander program) reads its command line, as far as this guard needs it:
#  * a value option (``--append-system-prompt <prompt>``) takes the NEXT argv token as its value even when that
#    token starts with "-" (so ``--append-system-prompt --setting-sources ""`` sets no setting sources);
#  * a variadic option (``--tools <tools...>``) takes its first value the same way, then every following token up
#    to the next one that looks like an option;
#  * ``--x=value`` carries its own value; ``--`` ends the options.
# Only flags listed here are accepted in a custom command. Any other flag could enable a channel or a data source
# this program cannot see (Chrome, IDE, agents, plugins, cloud sessions...), so it is refused unless the pass's
# unsafe override is set. A flag that is NOT listed is parsed as taking the next token as its value: that can hide
# a later flag from the checks, which can only make the guard stricter, never weaker.
BOOL_FLAGS = {"-p", "--print", "--strict-mcp-config", "--disable-slash-commands", "--no-chrome", "--verbose",
              "--safe-mode", "--bare", "--no-session-persistence", "--include-hook-events",
              "--include-partial-messages", "--exclude-dynamic-system-prompt-sections", "--restricted",
              # accepted by the parser so they can be reported by name (see FORBIDDEN_FLAGS)
              "--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--chrome", "--ide"}
VALUE_FLAGS = {"--output-format", "--input-format", "--permission-mode", "--permission-prompts", "--setting-sources",
               "--model", "--fallback-model", "--effort", "--append-system-prompt", "--system-prompt",
               "--system-prompt-snapshot", "--max-budget-usd", "--json-schema"}
# flags that take all following values up to the next flag (claude's `<tools...>` options)
VARIADIC = {"--tools", "--allowedTools", "--disallowedTools", "--add-dir", "--mcp-config", "--channels"}
ALIASES = {"--allowed-tools": "--allowedTools", "--disallowed-tools": "--disallowedTools"}
# flags a custom command may use in the read pass / relate pass
COMMON_ALLOWED = BOOL_FLAGS - {"--dangerously-skip-permissions", "--allow-dangerously-skip-permissions",
                               "--chrome", "--ide"}
COMMON_ALLOWED = COMMON_ALLOWED | VALUE_FLAGS | {"--tools", "--allowedTools", "--disallowedTools"}
# (they read files or folders, which only the relate pass may see)
RELATE_ONLY_FLAGS = {"--add-dir", "--mcp-config", "--system-prompt-file", "--append-system-prompt-file"}
# flags that bypass permissions, load extra settings/plugins/agents, or open a channel to a browser, an IDE or a
# remote service: named so the refusal says why. (Any other unlisted flag is refused as "unknown".)
FORBIDDEN_FLAGS = ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions", "--settings",
                   "--managed-settings", "--plugin-dir", "--plugin-dir-no-mcp", "--plugin-url", "--chrome", "--ide",
                   "--inherit-permission-mode", "--agent", "--agents", "--channels", "--sdk-url")
READ_ALLOWED = READ_TOOLS
RELATE_ALLOWED = RELATE_TOOLS


def is_claude(argv: Sequence[str]) -> bool:
    return bool(argv) and os.path.basename(argv[0]).lower() in ("claude", "claude.exe")


def _option_like(arg: str) -> bool:
    return len(arg) > 1 and arg.startswith("-")


def _scan(argv: Sequence[str]) -> tuple[dict[str, list[str]], list[str]]:
    """``(flags, positionals)``: the options of a claude command with their values, and every token that is
    neither an option nor an option value (also everything after ``--``)."""
    out: dict[str, list[str]] = {}
    extra: list[str] = []
    i = 1  # argv[0] is the program
    while i < len(argv):
        arg = argv[i]
        i += 1
        if arg == "--":
            extra += argv[i:]
            break
        if not _option_like(arg):
            extra.append(arg)
            continue
        name, eq, inline = arg.partition("=")
        name = ALIASES.get(name, name)
        values = out.setdefault(name, [])
        if eq:
            values.append(inline)
        elif name in VARIADIC:
            if i < len(argv):
                values.append(argv[i])
                i += 1
                while i < len(argv) and not _option_like(argv[i]):
                    values.append(argv[i])
                    i += 1
        elif name in BOOL_FLAGS:
            pass
        elif i < len(argv):  # a value option, or a flag this program does not know: the next token is its value
            values.append(argv[i])
            i += 1
    return out, extra


def parse_flags(argv: Sequence[str]) -> dict[str, list[str]]:
    """``{flag: [values...]}`` for a claude command, read the way claude reads it (see above). A flag without
    values maps to ``[]``; repeated flags accumulate. Unknown flags are included (taking the next token)."""
    return _scan(argv)[0]


def positional_args(argv: Sequence[str]) -> list[str]:
    """Tokens that are neither an option nor an option value. claude reads a first operand as a subcommand
    (``mcp``, ``plugin``, ``rc``...) or as the prompt; the prompt arrives on stdin, so a command has none."""
    return _scan(argv)[1]


def unknown_flags(argv: Sequence[str], pass_name: str) -> list[str]:
    """Flags of a custom claude command that are neither on the pass's allow-list nor explicitly forbidden."""
    allowed = COMMON_ALLOWED | RELATE_ONLY_FLAGS
    return [f for f in parse_flags(argv) if f not in allowed and f not in FORBIDDEN_FLAGS]


def relate_only_flags_used(argv: Sequence[str]) -> list[str]:
    """Flags the relate pass may use but a read command may not (they read files or folders)."""
    return [f for f in parse_flags(argv) if f in RELATE_ONLY_FLAGS]


def flag_values(argv: Sequence[str], *names: str) -> list[str] | None:
    """The values of a claude flag, or None when absent. Variadic flags end at the next flag."""
    flags = parse_flags(argv)
    found: list[str] | None = None
    for name in names:
        name = ALIASES.get(name, name)
        if name in flags:
            found = (found or []) + flags[name]
    return found


def split_tools(values: Sequence[str]) -> list[str]:
    """Tool names from flag values, split EXACTLY the way claude 2.1.303 splits ``--tools`` / ``--allowedTools`` /
    ``--disallowedTools`` (a port of its splitter): only ``,`` and ``" "`` separate entries, and only outside a
    parenthesis; a single boolean "inside parentheses" flag is set by ``(`` and cleared by the FIRST ``)``, so
    ``a(b) c`` is not nested-aware; leading whitespace is skipped and empty values are ignored. Because this
    flag is not a counter, the guard also refuses nested or unbalanced parentheses (``paren_problem``), the only
    inputs where a depth-counting reader and claude could disagree."""
    names: list[str] = []
    for value in values:
        if not value:
            continue
        current, inside = "", False
        for ch in value:
            if ch == "(":
                inside = True
                current += ch
            elif ch == ")":
                inside = False
                current += ch
            elif ch == ",":
                if inside:
                    current += ch
                else:
                    if current.strip():
                        names.append(current.strip())
                    current = ""
            elif ch == " ":
                if inside:
                    current += ch
                elif current.strip():
                    names.append(current.strip())
                    current = ""
            elif current or ch.strip():
                current += ch
        if current.strip():
            names.append(current.strip())
    return names


def paren_problem(values: Sequence[str]) -> str:
    """"" or why a tool list has nested or unbalanced parentheses (claude and this program could split it
    differently, so such a list is refused)."""
    for value in values:
        depth = 0
        for ch in value:
            if ch == "(":
                if depth:
                    return f"it has a nested parenthesis in a tool list ({value!r})"
                depth = 1
            elif ch == ")":
                if not depth:
                    return f"it has an unbalanced parenthesis in a tool list ({value!r})"
                depth = 0
        if depth:
            return f"it has an unbalanced parenthesis in a tool list ({value!r})"
    return ""


def base_name(tool: str) -> str:
    """``Bash(git *)`` -> ``bash``."""
    return tool.split("(", 1)[0].strip().lower()


def claude_tools(argv: Sequence[str]) -> list[str] | None:
    """The names given to ``--tools`` for a claude command, or None when the flag is absent."""
    values = flag_values(argv, "--tools")
    return None if values is None else split_tools(values)


def has_mcp_config(argv: Sequence[str]) -> bool:
    return "--mcp-config" in parse_flags(argv)


def has_isolation(argv: Sequence[str]) -> bool:
    """True when the command loads none of the user's settings: ``--setting-sources ""`` (or only unknown/empty),
    ``--safe-mode`` or ``--bare``."""
    flags = parse_flags(argv)
    if "--safe-mode" in flags or "--bare" in flags:
        return True
    sources = flags.get("--setting-sources")
    if sources is None:
        return False
    return not any(s.strip() for v in sources for s in v.split(","))


def is_server_wide(tool: str) -> bool:
    """``mcp__x``, ``mcp__x__*`` or any wildcard: an allow rule that covers more than one named tool."""
    tool = tool.strip()
    if not tool.lower().startswith("mcp__"):
        return False
    rest = tool[5:]
    return "*" in tool or "__" not in rest or rest.endswith("__")


def _tool_problem(tool: str, allowed: Sequence[str], where: str, mcp_ok: bool) -> str:
    name = base_name(tool)
    if name in {a.lower() for a in allowed}:
        return ""
    if name.startswith("mcp__"):
        return "" if mcp_ok else f"{where} names an MCP tool ({tool}), and the read pass must have no MCP tools"
    if name.startswith("bash"):
        return f"{where} allows Bash, which can reach the network"
    if name in ("webfetch", "websearch"):
        return f"{where} names WebFetch or WebSearch"
    if name == "default":
        return f"{where} is `default`, which gives claude all its tools including Bash and web access"
    return f"{where} names {tool!r}, which is not an allowed tool for this pass ({', '.join(allowed)})"


def _common_problems(argv: Sequence[str], pass_name: str) -> list[str]:
    flags = parse_flags(argv)
    problems = [f"it uses {flag}, which changes permissions, settings, plugins or agents, or opens a channel to a "
                "browser, an IDE or a remote service, so this program cannot check it"
                for flag in FORBIDDEN_FLAGS if flag in flags]
    if pass_name == "read" and (misplaced := relate_only_flags_used(argv)):
        problems.append("it uses " + ", ".join(misplaced) + ", which is only allowed in the relate pass (the read pass "
                        "must not see your files)")
    unknown = unknown_flags(argv, pass_name)
    if unknown:
        problems.append("it uses " + ", ".join(unknown) + ", which this program does not know (a custom command may "
                        "only use flags it can check; an unknown one could enable tools, a browser or extra settings)")
    stray = positional_args(argv)
    if stray:
        problems.append("it has an extra argument (" + ", ".join(repr(t) for t in stray[:3]) + "), which claude can "
                        "read as a subcommand or a prompt; the prompt arrives on stdin, so a command has none")
    if "--permission-mode" in flags and any(v == "bypassPermissions" for v in flags["--permission-mode"]):
        problems.append("it uses --permission-mode bypassPermissions")
    return problems


def _tool_problems(argv: Sequence[str], allowed: Sequence[str], mcp_ok: bool) -> list[str]:
    flags = parse_flags(argv)
    problems: list[str] = []
    for flag in ("--tools", "--allowedTools", "--disallowedTools"):
        if p := paren_problem(flags.get(flag, [])):
            problems.append(p.replace("a tool list", f"its {flag} list"))
    if any("(" in t for t in claude_tools(argv) or []):
        problems.append("its --tools list has a tool with a specifier in parentheses; --tools takes plain tool names")
    tools = claude_tools(argv)
    if tools is None:
        problems.append("it has no --tools list, so claude would get its full default tools including web and file access")
    else:
        problems += [p for t in tools if (p := _tool_problem(t, allowed, f"its --tools list ({t})", mcp_ok))]
    for t in split_tools(flags.get("--allowedTools", [])):
        if p := _tool_problem(t, allowed, f"its --allowedTools list ({t})", mcp_ok):
            problems.append(p)
    return problems


def _mcp_problem_read(argv: Sequence[str]) -> str:
    """--tools limits only the built-in tools: without --strict-mcp-config claude also loads your MCP servers."""
    flags = parse_flags(argv)
    if "--strict-mcp-config" not in flags:
        return "it lacks --strict-mcp-config, so claude would also load your MCP servers"
    if "--mcp-config" in flags:
        return "it adds MCP servers with --mcp-config"
    if "--add-dir" in flags:
        return "it adds folders with --add-dir, but the read pass must not see your files"
    return ""


def _mcp_problem_relate(argv: Sequence[str]) -> str:
    flags = parse_flags(argv)
    if "--strict-mcp-config" not in flags:
        return "it lacks --strict-mcp-config, so claude would also load your MCP servers"
    # a server-wide rule is refused whether or not this command adds servers itself: it would also allow the
    # write tools of any server that loads (for example through a wrapper that adds --mcp-config)
    for flag in ("--allowedTools", "--tools"):
        for t in split_tools(flags.get(flag, [])):
            if is_server_wide(t):
                return f"its {flag} list allows a whole MCP server or a wildcard ({t}); name each read-only tool"
    if "--mcp-config" not in flags:
        return ""
    mode = flags.get("--permission-mode")
    if not mode or mode[-1] != "dontAsk":
        return ("it adds MCP servers with --mcp-config but not --permission-mode dontAsk, so every tool of "
                "those servers would be allowed")
    if not flags.get("--allowedTools"):
        return "it adds MCP servers with --mcp-config but has no --allowedTools list that names the MCP tools"
    return ""


def relate_mcp_warning(argv: Sequence[str]) -> str:
    """A custom Claude relate command with --mcp-config passed the guard, but its tools cannot be checked."""
    if is_claude(argv) and has_mcp_config(argv):
        return ("the custom relate command uses --mcp-config: its MCP tools cannot be verified by this program. "
                "Make sure every tool in --allowedTools only reads, takes no URL and sends nothing out.")
    return ""


def relate_network_problem(argv: Sequence[str]) -> str:
    """Why this relate command may reach the network, or "" if it looks safe (known claude commands only)."""
    if not is_claude(argv):
        return ""
    problems = _common_problems(argv, "relate") + _tool_problems(argv, RELATE_ALLOWED, True)
    if not problems:
        problems = [p] if (p := _mcp_problem_relate(argv)) else []
    return "; and ".join(problems[:3])


def read_private_problem(argv: Sequence[str]) -> str:
    """Why this read command may reach your private data, or "" if it looks safe (known claude commands only)."""
    if not is_claude(argv):
        return ""
    problems = _common_problems(argv, "read") + _tool_problems(argv, READ_ALLOWED, False)
    if not problems:
        if p := _mcp_problem_read(argv):
            problems.append(p)
        elif not has_isolation(argv):
            problems.append("it loads your Claude settings, CLAUDE.md and hooks (add --setting-sources \"\" and "
                            "--disable-slash-commands)")
    return "; and ".join(problems[:3])


def relate_read_warning(argv: Sequence[str]) -> str:
    """A custom Claude relate command whose ``--allowedTools`` allows Read, Grep or Glob without limiting it to the
    context folders: bare, ``(*)``, or with an absolute / home path rule such as ``Read(//**)``. Under ``dontAsk``
    the built-in tools without an allow rule only reach the working directory and the ``--add-dir`` folders; such a
    rule widens that to any file the user can read. A warning, not a refusal: the relate pass has no network."""
    if not is_claude(argv):
        return ""
    wide = []
    for tool in split_tools(parse_flags(argv).get("--allowedTools", [])):
        if base_name(tool) not in ("read", "grep", "glob"):
            continue
        spec = tool.partition("(")[2].rstrip(")").strip() if "(" in tool else ""
        if not spec or spec.startswith(("/", "~")) or spec in ("*", "**"):
            wide.append(tool)
    if not wide:
        return ""
    return ("the custom relate command's --allowedTools has " + ", ".join(wide) + ": the relate agent can read any "
            "file you can read, not only the context folders. Drop the rule to keep it inside the --add-dir folders.")


def relate_isolation_warning(argv: Sequence[str]) -> str:
    if is_claude(argv) and not has_isolation(argv):
        return ("the custom relate command loads your Claude settings, hooks and permission allow rules, which can "
                "widen its tools (add --setting-sources \"\" and --disable-slash-commands, or use agent = \"claude\")")
    return ""


def unverified_warning(argv: Sequence[str], pass_name: str) -> str:
    """For a custom command this program cannot read (anything but a direct `claude`): the loud caveat."""
    if not argv or is_claude(argv):
        return ""
    looks_like_claude = any(re.search(r"(^|[/\\])claude(\.exe)?$|claude-code", a.lower()) for a in argv[1:4])
    extra = (" It looks like a wrapper around Claude Code (env, npx, a script): the guard only reads a direct `claude` "
             "command, so call claude directly or use [enrich] agent = \"claude\"." if looks_like_claude else "")
    rule = ("must have the web but no access to your files" if pass_name == "read" else
            "must see your files but have no network")
    return (f"cannot verify isolation: the custom {pass_name} command ({os.path.basename(argv[0])}) is not a direct "
            f"claude command, so this program cannot check its tools. The {pass_name} pass {rule}; check it "
            f"yourself.{extra}")


def with_add_dirs(argv: list[str], folders: Sequence[Path]) -> list[str]:
    """For a custom claude command, scope file reads to the context folders (cwd alone is not enough)."""
    if not is_claude(argv) or "--add-dir" in argv:
        return argv
    extra: list[str] = []
    for folder in folders:
        extra += ["--add-dir", str(folder)]
    return argv + extra
