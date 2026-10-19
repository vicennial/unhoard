"""Where data and configuration live, and the typed view of ``config.toml``.

Paths are resolved to absolute form exactly once. Agents, cron jobs and MCP
servers often run with a different HOME (or a sandboxed one) than the shell
where you set things up, so anything that launches this package from such a
context should export ``UNHOARD_HOME`` and ``UNHOARD_CONFIG``
explicitly. The schedule examples do that.
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

APP = "unhoard"
ENV_HOME = "UNHOARD_HOME"
ENV_CONFIG = "UNHOARD_CONFIG"
ENV_TOKEN = "X_BEARER_TOKEN"
ENV_CLIENT_ID = "X_CLIENT_ID"  # `login x`: the client id of your own X developer app
ENV_CLIENT_SECRET = "X_CLIENT_SECRET"  # `login x`, confidential clients only: a secret, kept out of agent environments

DEFAULTS: dict[str, dict[str, Any]] = {
    "x": {"user_id": "", "bearer_token": "", "client_id": "", "client_secret": "", "max_items": 500,
          "api_base": "https://api.x.com/2"},
    "enrich": {
        "agent": "",  # "claude" or "codex": the program builds both commands at run time
        "command": [],  # legacy: one command for both passes (use read_command / relate_command)
        "read_command": [],
        "relate_command": [],
        "allow_network_in_relate": False,
        "allow_private_in_read": False,
        "max_tasks": 3,
        "timeout": 300,  # seconds for the read pass
        "relate_timeout": 0,  # 0 = at least 600 s (the relate agent searches your folders), else max(timeout, 600)
        "about_me_file": "",
        "labels": [],
    },
    "context": {"folders": [], "memory_folders": [], "notes_folders": [], "index_files": [], "repos": [],
                "mcp_servers": [], "mcp_tools": [], "commands": [], "repo_commits": 15, "max_chars": 12000,
                "command_timeout": 30},
    "queue": {"daily_cards": 3, "later_days": 3},
    "launch": {"adapter": "auto"},
    "omnigent": {
        "api": "http://127.0.0.1:6767",
        "agent_id": "",
        "project": "X Bookmarks",
        "host_id": "",
        "workspace": "",
        "harness": "",
        "model": "",
        "reasoning_effort": "",
    },
    "command": {"argv": []},
    "server": {"port": 8765, "show_media": True, "allowed_hosts": [], "require_tailscale_identity": True},
}


@dataclass(frozen=True)
class Paths:
    data_dir: Path
    config_file: Path

    @property
    def db_file(self) -> Path:
        return self.data_dir / "bookmarks.sqlite3"

    @property
    def scratch_dir(self) -> Path:
        """Neutral working directory for the enrichment agent (never the user's repo)."""
        return self.data_dir / "scratch"


def resolve_paths(env: Mapping[str, str] | None = None) -> Paths:
    env = os.environ if env is None else env
    home = Path(os.path.expanduser("~"))
    if env.get(ENV_HOME):
        data = Path(env[ENV_HOME]).expanduser()
    else:
        data = Path(env.get("XDG_DATA_HOME") or home / ".local" / "share") / APP
    if env.get(ENV_CONFIG):
        cfg = Path(env[ENV_CONFIG]).expanduser()
        if cfg.is_dir():
            cfg = cfg / "config.toml"
    else:
        cfg = Path(env.get("XDG_CONFIG_HOME") or home / ".config") / APP / "config.toml"
    return Paths(data.resolve(), cfg.resolve())


_HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?")
ALLOWED_HOSTS_RULE = ("a host name such as mybox.tail1234.ts.net: letters, digits, hyphens and dots only, no scheme, "
                      "port, path or wildcard, and not an IP address")


def check_allowed_host(entry: object) -> tuple[str | None, str | None]:
    """``(normalised, None)`` for a valid ``[server] allowed_hosts`` entry, else ``(None, why)``.

    The entry is a plain DNS name (a MagicDNS name, for example). It is lower-cased. A scheme, port, path, wildcard,
    user part or IP address is refused: the match is by name only, so a loose entry would widen who may talk to the
    server. The loopback names are always allowed and need no entry.
    """
    if not isinstance(entry, str) or not entry.strip():
        return None, "must be a non-empty string"
    name = entry.strip().lower()
    if "*" in name or "?" in name:
        return None, "wildcards are not allowed"
    if "://" in name or "/" in name or "@" in name or "?" in name or "#" in name:
        return None, "give the host name only, without a scheme, path or user"
    if ":" in name:
        return None, "give the host name only; ports are matched automatically (and IPv6 addresses are not accepted)"
    if name.endswith("."):
        return None, "remove the trailing dot"
    if len(name) > 253 or not all(_HOST_LABEL.fullmatch(label) for label in name.split(".")):
        return None, "is not a valid host name"
    if re.fullmatch(r"[0-9.]+", name):
        return None, "IP addresses are not accepted; use the host name"
    return name, None


class ConfigError(ValueError):
    """The configuration file is unreadable or has a value of the wrong type."""


class Config:
    """``config.toml`` merged over defaults. Unknown sections and keys are kept, not rejected."""

    def __init__(self, data: Mapping[str, Any], paths: Paths, env: Mapping[str, str] | None = None):
        self.paths = paths
        self._env = os.environ if env is None else env
        self._data = {name: {**section, **(data.get(name) or {})} for name, section in DEFAULTS.items()}
        for name, value in data.items():
            if name not in self._data and isinstance(value, dict):
                self._data[name] = dict(value)
        self.problems = self._check()

    @classmethod
    def load(cls, paths: Paths | None = None, env: Mapping[str, str] | None = None) -> "Config":
        paths = paths or resolve_paths(env)
        data: dict[str, Any] = {}
        if paths.config_file.exists():
            try:
                data = tomllib.loads(paths.config_file.read_text(encoding="utf-8"))
            except (OSError, tomllib.TOMLDecodeError) as exc:
                raise ConfigError(f"cannot read {paths.config_file}: {exc}") from exc
        return cls(data, paths, env)

    def section(self, name: str) -> dict[str, Any]:
        return self._data.get(name, {})

    def get(self, section: str, key: str, default: Any = None) -> Any:
        return self._data.get(section, {}).get(key, default)

    @property
    def x_token(self) -> str:
        """Token from the environment first, so it never has to be written to disk."""
        return self._env.get(ENV_TOKEN) or str(self.get("x", "bearer_token", "") or "")

    @property
    def x_token_origin(self) -> str | None:
        """Where ``x_token`` comes from: "env", "config" or None. (The token of ``login x`` is separate: see xauth.)"""
        if self._env.get(ENV_TOKEN):
            return "env"
        return "config" if str(self.get("x", "bearer_token", "") or "") else None

    def allowed_hosts(self) -> list[str]:
        """The valid ``[server] allowed_hosts`` entries, lower-cased, without duplicates. Invalid ones are left out
        (``problems`` lists them), so a typo never widens access."""
        values = self.get("server", "allowed_hosts", [])
        out: list[str] = []
        for value in values if isinstance(values, list) else []:
            name, _why = check_allowed_host(value)
            if name and name not in out:
                out.append(name)
        return out

    def x_client(self) -> tuple[str, str]:
        """``(client_id, client_secret)`` for ``login x``: environment first, then ``[x]``. The secret may be empty."""
        return (self._env.get(ENV_CLIENT_ID) or str(self.get("x", "client_id", "") or ""),
                self._env.get(ENV_CLIENT_SECRET) or str(self.get("x", "client_secret", "") or ""))

    def about_me(self, limit: int = 6000) -> str:
        path = str(self.get("enrich", "about_me_file", "") or "")
        if not path:
            return ""
        try:
            return Path(path).expanduser().read_text(encoding="utf-8")[:limit].strip()
        except OSError:
            return ""

    def _paths(self, key: str) -> list[Path]:
        values = self.get("context", key, [])
        if not isinstance(values, list):
            return []
        return [Path(f).expanduser().resolve() for f in values if isinstance(f, str) and f.strip()]

    def context_folders(self) -> list[Path]:
        """Absolute paths of ``[context] folders`` (whether or not they exist)."""
        return self._paths("folders")

    def context_sources(self) -> list[tuple[str, Path]]:
        """Every folder the relate agent may read, as (kind, path): memory, notes, folder, repo. Existing ones only."""
        out: list[tuple[str, Path]] = []
        for kind, key in (("memory", "memory_folders"), ("notes", "notes_folders"), ("folder", "folders"),
                          ("repo", "repos")):
            for path in self._paths(key):
                if path.is_dir() and all(path != p for _, p in out):
                    out.append((kind, path))
        return out

    def index_files(self) -> list[Path]:
        return [p for p in self._paths("index_files") if p.is_file()]

    def relate_timeout(self) -> int:
        """Seconds for the relate pass: it searches folders and calls tools, so it gets at least 600."""
        explicit = int(self.get("enrich", "relate_timeout", 0) or 0)
        return explicit if explicit > 0 else max(int(self.get("enrich", "timeout", 300)), 600)

    def _check(self) -> list[str]:
        problems = []
        expect = {
            ("x", "max_items"): int, ("x", "client_id"): str, ("x", "client_secret"): str, ("enrich", "max_tasks"): int, ("enrich", "timeout"): int,
            ("enrich", "relate_timeout"): int, ("enrich", "agent"): str,
            ("context", "memory_folders"): list, ("context", "notes_folders"): list, ("context", "index_files"): list,
            ("context", "mcp_servers"): list, ("context", "mcp_tools"): list,
            ("enrich", "command"): list, ("enrich", "read_command"): list, ("enrich", "relate_command"): list,
            ("enrich", "allow_network_in_relate"): bool, ("enrich", "allow_private_in_read"): bool, ("enrich", "labels"): list, ("command", "argv"): list,
            ("context", "folders"): list, ("context", "repos"): list, ("context", "commands"): list,
            ("context", "repo_commits"): int, ("context", "max_chars"): int, ("context", "command_timeout"): int,
            ("queue", "daily_cards"): int, ("queue", "later_days"): int, ("server", "port"): int,
            ("server", "allowed_hosts"): list, ("server", "require_tailscale_identity"): bool,
        }
        for (section, key), kind in expect.items():
            value = self.get(section, key)
            if not isinstance(value, kind) or (isinstance(value, bool) and kind is not bool):
                problems.append(f"[{section}] {key} must be {kind.__name__}")
        for section, key in (("enrich", "command"), ("enrich", "read_command"), ("enrich", "relate_command"),
                             ("command", "argv"), ("context", "folders"), ("context", "repos"),
                             ("context", "memory_folders"), ("context", "notes_folders"), ("context", "index_files"),
                             ("context", "mcp_servers"), ("context", "mcp_tools")):
            value = self.get(section, key)
            if isinstance(value, list) and not all(isinstance(x, str) for x in value):
                problems.append(f"[{section}] {key} must be a list of strings"
                                + (" (an argv, not a shell line)" if section == "enrich" or section == "command" else ""))
        commands = self.get("context", "commands")
        if isinstance(commands, list) and not all(
                isinstance(c, list) and c and all(isinstance(x, str) for x in c) for c in commands):
            problems.append("[context] commands must be a list of argv lists, for example [[\"gbrain\", \"query\", \"{topics}\"]]")
        hosts = self.get("server", "allowed_hosts")
        if isinstance(hosts, list):
            for value in hosts:
                _name, why = check_allowed_host(value)
                if why:
                    problems.append(f"[server] allowed_hosts: {value!r} {why}; each entry must be {ALLOWED_HOSTS_RULE}")
        for key in ("folders", "memory_folders", "notes_folders", "repos"):
            for folder in self._paths(key):
                if not folder.is_dir():
                    problems.append(f"[context] {key}: {folder} is not a directory")
        if self.get("enrich", "agent") not in ("", "claude", "codex"):
            problems.append('[enrich] agent must be "claude" or "codex" (or empty to use read_command / relate_command)')
        if self.get("launch", "adapter") not in ("", "auto", "print", "command", "omnigent"):  # "" means auto
            problems.append("[launch] adapter must be auto, print, command or omnigent")
        return problems


TEMPLATE = """\
# unhoard configuration. Everything stays on this machine.
# Cron jobs and agents may run with a different HOME: export
# UNHOARD_HOME and UNHOARD_CONFIG for them.

[x]
# Numeric id of your X account. Leave empty to look it up with the token.
user_id = {user_id}
# Prefer the X_BEARER_TOKEN environment variable (an OAuth 2.0 user token with
# the bookmark.read scope) over writing a token here.
# bearer_token = ""
max_items = 500

[enrich]
# Enrichment runs in two passes so no single agent holds untrusted text,
# your private context and an outbound channel at the same time.
#
# Pass 1 "read": sees only the saved post. May read the web, never your files.
# Pass 2 "relate": sees only pass 1's checked summary and your context ([context]
# below). May read your folders and call read-only memory tools, has NO network.
#
# agent = "claude" or "codex": this program builds both commands each time it
# runs, so later fixes reach you. Run `unhoard doctor` to check them.
agent = {agent}
# Custom commands (an argv list; prompt on stdin, JSON on stdout) replace the
# preset for that pass. Leave empty to use the preset.
read_command = {read_command}
relate_command = {relate_command}
# Unsafe: lets a relate_command that may reach the network start anyway.
# allow_network_in_relate = false
# Unsafe: lets a read_command that may reach files or MCP servers start anyway.
# allow_private_in_read = false
max_tasks = 3
# Seconds for the read pass. The relate pass searches your folders and calls
# tools, so it waits at least 600 s (set relate_timeout to change that).
timeout = 300
# relate_timeout = 600
# Optional plain-text file describing you, your work and goals. Without it, the
# instruction and memory files listed under [context] index_files are used.
about_me_file = ""
# Labels the agent may use to group tasks, for example ["work", "personal"].
labels = []

[context]
# What pass 2 may use to connect a post to what you do. `unhoard
# context discover` (and setup) fills these in; edit them freely.
#
# Folders the relate agent may read (and only these): agent memory, note vaults,
# other folders, and Git repositories (their last commit subjects go in the prompt too).
memory_folders = []
notes_folders = []
folders = []
repos = []
# Files whose text is added to the prompt (capped), such as CLAUDE.md and MEMORY.md.
index_files = []
# Names of user-level Claude MCP servers (~/.claude.json) whose read-only memory
# tools the relate agent may call. Only servers this program knows are used.
mcp_servers = []
# Extra tools by hand, e.g. ["mcp__myserver__search"]. Your responsibility: only
# tools that read, take no URL and send nothing out.
mcp_tools = []
# repo_commits = 15
# Read-only commands run by this program (no shell). "{{topics}}" becomes ONE argument:
# the topics from pass 1 joined by spaces. Commands MUST NOT change anything.
# commands = [["gbrain", "query", "{{topics}}"]]
# Upper bound on the text added to the pass 2 prompt (each source is cut fairly).
# max_chars = 12000
# command_timeout = 30

[queue]
daily_cards = 3
later_days = 3

[launch]
# auto: an Omnigent session when Omnigent is running and an agent_id is chosen below, else show the brief
# print: show the brief   command: pipe it to a program   omnigent: always create an Omnigent session
adapter = {adapter}

[omnigent]
api = "http://127.0.0.1:6767"
# An agent id registered on your Omnigent server: curl {{api}}/v1/agents
agent_id = ""
# Omnigent project (a folder in the sidebar) for launched sessions; created on the first Go.
# Empty: no project.
project = "X Bookmarks"
# host_id = ""        # curl {{api}}/v1/hosts
# workspace = ""      # absolute path on that host
# harness = ""        # for example "claude-sdk" (overrides the agent default)
# model = ""
# reasoning_effort = ""

[command]
# Used when adapter = "command". The brief is written to the program's stdin.
argv = []

[server]
port = 8765
# The server listens on 127.0.0.1 only. To reach it from your phone over a private network (see docs/phone.md), list
# the host name it is reached by, for example your Tailscale name. Host names only: no scheme, port or wildcard.
# `unhoard phone` prints the steps and can write this for you.
# allowed_hosts = ["mybox.tail1234.ts.net"]
# Requests that arrive by one of those names must carry the Tailscale-User-Login header that `tailscale serve` adds
# (Funnel requests carry none, so a mistaken funnel gets nothing). Turn this off only behind another HTTPS proxy
# that you trust to be the only way in; see docs/phone.md before you do.
# require_tailscale_identity = true
# Show post images? They are loaded by your browser from X's image servers.
show_media = true
"""
