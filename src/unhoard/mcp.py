"""Memory MCP servers for the relate pass: a built-in registry of read-only tools and a generated config file.

Claude's ``--strict-mcp-config`` makes it load only the servers in the ``--mcp-config`` file we generate, and
``--permission-mode dontAsk`` with an ``--allowedTools`` list makes every other tool of those servers a denial
(checked with claude 2.1.303: the tool list still names all tools of a loaded server, but only the allowed ones
can be called).

REGISTRY RULE. A tool is listed for a known memory server only if it
  * does not write or change anything (no put/capture/remember/forget/job tools),
  * does not take a URL or fetch anything (no ``fetch``/``think``/``synthesize``/file tools), and
  * does not send messages.
Unknown servers are never enabled by this program. The user can add tools by hand with
``[context] mcp_tools = ["mcp__<server>__<tool>"]``; that is the user's responsibility.

A cloud-hosted memory server receives the query text (derived from the bookmark). That is the user's own
provider, not a destination an attacker chose.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import signal
import tempfile
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .agents import is_server_wide, split_tools

# registry key -> (server names, regexes over "command args url", read-only tools)
REGISTRY: dict[str, dict[str, Any]] = {
    "gbrain": {
        "names": ("gbrain",),
        "patterns": (r"\bgbrain\b",),
        "tools": ("query", "search", "get_page", "get_links", "get_backlinks", "get_timeline", "list_pages",
                  "resolve_slugs"),
    },
    "basic-memory": {
        "names": ("basic-memory", "basic_memory"),
        "patterns": (r"basic[-_ ]memory",),
        "tools": ("search_notes", "read_note", "build_context", "recent_activity"),
    },
    "memory": {  # modelcontextprotocol/servers "server-memory" (knowledge graph)
        "names": ("memory",),
        "patterns": (r"server-memory",),
        "tools": ("read_graph", "search_nodes", "open_nodes"),
    },
}

HOW_TO_ADD = ('add its read-only tools by hand: [context] mcp_tools = ["mcp__<server>__<tool>", ...] '
              "(your responsibility: only tools that read, take no URL and send nothing out)")


_BAD_TOOL_CHARS = re.compile(r"[\s,()*]")


def server_key(name: str) -> str:
    """Claude's spelling of a server name inside tool names (``mcp__<server>__<tool>``)."""
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)


def tool_name(server: str, tool: str) -> str:
    return f"mcp__{server_key(server)}__{tool}"


def claude_config_path(env: Mapping[str, str] | None = None, home: Path | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get("CLAUDE_CONFIG_DIR"):
        return Path(env["CLAUDE_CONFIG_DIR"]).expanduser() / ".claude.json"
    return (home or Path.home()) / ".claude.json"


def load_user_servers(env: Mapping[str, str] | None = None, home: Path | None = None) -> dict[str, dict]:
    """User-scope ``mcpServers`` from Claude's config (top level only; project-scoped servers are ignored)."""
    try:
        data = json.loads(claude_config_path(env, home).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    if not isinstance(servers, dict):
        return {}
    return {str(k): v for k, v in servers.items() if isinstance(v, dict)}


def match_registry_detail(name: str, server: Mapping[str, Any]) -> tuple[str | None, bool]:
    """``(registry key, confirmed)``. ``confirmed`` is True when the server's command / args / url also looks like
    the known program; a match on the NAME alone is not confirmed (any server can be called ``memory``)."""
    haystack = " ".join([str(server.get("command") or ""), " ".join(str(a) for a in server.get("args") or []),
                         str(server.get("url") or "")]).lower()
    lowered = name.lower()
    by_name = next((key for key, entry in REGISTRY.items() if lowered in entry["names"]), None)
    if by_name and any(re.search(p, haystack) for p in REGISTRY[by_name]["patterns"]):
        return by_name, True
    for key, entry in REGISTRY.items():
        if any(re.search(p, haystack) for p in entry["patterns"]):
            return key, True
    return by_name, False


def match_registry(name: str, server: Mapping[str, Any]) -> str | None:
    """The registry key for a server, matched by its command / args / url pattern or by its name."""
    return match_registry_detail(name, server)[0]


def registry_tools(server_name: str, key: str) -> list[str]:
    return [tool_name(server_name, t) for t in REGISTRY[key]["tools"]]


def discover_servers(servers: Mapping[str, Mapping[str, Any]]) -> tuple[dict[str, str], list[str]]:
    """Split user servers into ``{name: registry key}`` (usable) and the names that are not recognised."""
    known: dict[str, str] = {}
    unknown: list[str] = []
    for name, server in servers.items():
        key = match_registry(name, server)
        if key:
            known[name] = key
        else:
            unknown.append(name)
    return known, sorted(unknown)


@dataclass
class McpSelection:
    """What the relate pass may use: server entries to copy and the exact tool names to allow."""
    servers: dict[str, dict] = field(default_factory=dict, repr=False)  # entries can hold tokens: never print them
    tools: list[str] = field(default_factory=list)
    not_used: list[str] = field(default_factory=list)  # in the user's Claude config but not enabled
    missing: list[str] = field(default_factory=list)  # enabled in [context] but absent from the Claude config
    rejected: list[str] = field(default_factory=list)  # manual mcp_tools refused (server-wide or wildcard)
    warnings: list[str] = field(default_factory=list)  # e.g. a server matched by name only

    def __bool__(self) -> bool:
        return bool(self.servers and self.tools)

    def summary(self) -> dict:
        per_server: dict[str, list[str]] = {}
        for name in self.servers:
            prefix = f"mcp__{server_key(name)}__"
            per_server[name] = [t[len(prefix):] if t.startswith(prefix) else t for t in self.tools
                                if t.startswith(prefix) or t == prefix[:-2]]
        return {"in_use": per_server, "not_used": self.not_used, "missing": self.missing,
                "rejected_tools": self.rejected, "warnings": self.warnings,
                "how_to_add_tools": HOW_TO_ADD if self.not_used else ""}


def select(enabled: Sequence[str], manual_tools: Sequence[str], servers: Mapping[str, Mapping[str, Any]]) -> McpSelection:
    """Resolve ``[context] mcp_servers`` (names) and ``mcp_tools`` (manual) against the user's Claude servers.

    A manual tool must name ONE tool (``mcp__server__tool``): ``mcp__server`` and wildcards allow a whole server,
    including its write tools, and are rejected. Entries are split with ``agents.split_tools``, an exact port of
    claude 2.1.303's splitter (only commas and spaces separate, outside one parenthesis), so one config string
    cannot smuggle in a second tool or a server-wide form; an entry that still holds whitespace, a comma, a
    parenthesis or a wildcard is rejected as well. (The entries end up inside the preset's ``--allowedTools``.)
    """
    by_key = {server_key(n): n for n in servers}
    chosen: dict[str, dict] = {}
    tools: list[str] = []
    missing: list[str] = []
    rejected: list[str] = []
    warnings: list[str] = []
    for name in enabled:
        real = name if name in servers else by_key.get(server_key(name))
        if real is None:
            missing.append(name)
            continue
        key, confirmed = match_registry_detail(real, servers[real])
        if key is None:  # a name in the config that is not a known memory server: tools must be listed by hand
            continue
        if not confirmed:
            warnings.append(f"MCP server {real!r} was matched to the {key!r} read-only tool list by its name only; "
                            f"its command / url does not look like {key}. Check that it really is that program.")
        chosen[real] = dict(servers[real])
        tools += registry_tools(real, key)
    for tool in split_tools(manual_tools):
        if is_server_wide(tool) or _BAD_TOOL_CHARS.search(tool):
            rejected.append(tool)
            continue
        # server names may contain underscores, so match against the known servers (longest first)
        owner = next((k for k in sorted(by_key, key=len, reverse=True) if tool.startswith(f"mcp__{k}__")), None)
        if owner is None:
            if tool.startswith("mcp__"):
                missing.append(tool)
            continue
        chosen.setdefault(by_key[owner], dict(servers[by_key[owner]]))
        tools.append(tool)
    tools = list(dict.fromkeys(tools))
    if rejected:
        warnings.append("ignored [context] mcp_tools that allow a whole server, use a wildcard or are not a single tool name: "
                        + ", ".join(sorted(set(rejected))) + ". List each read-only tool as mcp__<server>__<tool>.")
    return McpSelection(chosen, tools, sorted(n for n in servers if n not in chosen), sorted(set(missing)),
                        sorted(set(rejected)), warnings)


# --- tools each enabled server exposes (names only), remembered by `doctor` for --disallowedTools --------------

def exposed_cache_path(data_dir: Path) -> Path:
    return Path(data_dir) / "mcp-tools.json"


def load_exposed(data_dir: Path) -> dict[str, list[str]]:
    try:
        data = json.loads(exposed_cache_path(data_dir).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): [str(t) for t in v if isinstance(t, str)] for k, v in data.items() if isinstance(v, list)}


def save_exposed(data_dir: Path, tools: Sequence[str]) -> None:
    """Remember the ``mcp__server__tool`` names an init event listed (names only; no config, no secrets).

    The new names are merged into the old ones and never replace them: once a tool is denied with
    ``--disallowedTools`` claude stops listing it, and a shrinking list would stop denying it on the next run.
    """
    per_server = load_exposed(data_dir)
    for tool in tools:
        parts = tool.split("__")
        if tool.startswith("mcp__") and len(parts) >= 3:
            names = per_server.setdefault(parts[1], [])
            if tool not in names:
                names.append(tool)
    path = exposed_cache_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(per_server, handle)
    os.replace(tmp, path)


def deny_tools(selection: McpSelection, exposed: Mapping[str, Sequence[str]]) -> list[str]:
    """Tools of the enabled servers that were seen but are not selected: denied explicitly as a second layer."""
    out: list[str] = []
    for name in selection.servers:
        out += [t for t in exposed.get(server_key(name), []) if t not in selection.tools]
    return sorted(dict.fromkeys(out))


# SIGHUP does not exist on Windows
_SIGNALS = tuple(getattr(signal, name) for name in ("SIGTERM", "SIGINT", "SIGHUP") if hasattr(signal, name))


@contextlib.contextmanager
def _cleanup_on_signals(folder: str) -> Iterator[None]:
    """Delete ``folder`` if the process gets SIGTERM / SIGINT / SIGHUP while it exists (a finally block does not
    run when SIGTERM kills the process), then unwind with SystemExit so callers' cleanup runs too. Only installable from the main thread; elsewhere it does nothing."""
    previous: dict[int, Any] = {}

    def handler(signum: int, frame: Any) -> None:
        shutil.rmtree(folder, ignore_errors=True)
        old = previous.get(signum)
        if callable(old):  # e.g. SIGINT's default handler raises KeyboardInterrupt
            old(signum, frame)
        raise SystemExit(128 + signum)  # unwind, so the agent's process group is killed as well

    if threading.current_thread() is threading.main_thread():
        for signum in _SIGNALS:
            try:
                if signal.getsignal(signum) == signal.SIG_IGN:
                    continue  # ignored on purpose (for example SIGHUP under nohup): leave it ignored
                previous[signum] = signal.signal(signum, handler)
            except (ValueError, OSError):
                pass
    try:
        yield
    finally:
        for signum, old in previous.items():
            try:
                signal.signal(signum, old if old is not None else signal.SIG_DFL)
            except (ValueError, OSError):
                pass


@contextlib.contextmanager
def config_file(selection: McpSelection) -> Iterator[str | None]:
    """Write the generated ``--mcp-config`` file (mode 0600, private temp dir); delete it afterwards, also when
    the process is stopped by a signal."""
    if not selection:
        yield None
        return
    folder = tempfile.mkdtemp(prefix="obm-mcp-")  # mode 0700
    try:
        with _cleanup_on_signals(folder):
            path = os.path.join(folder, "mcp.json")
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({"mcpServers": selection.servers}, handle)
            yield path
    finally:
        shutil.rmtree(folder, ignore_errors=True)
