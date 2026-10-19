"""Find the places that hold what the reader does: memory folders, notes, repos, instruction files, memory MCP.

Everything here only looks (directory listings, ``git log``, reading ``~/.claude.json``). The result is shown to
the user and, once accepted, written into ``[context]`` of ``config.toml`` so it can be inspected and edited.
"""
from __future__ import annotations

import collections
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from . import mcp

WALK_SECONDS = 3.0        # time box for the directory walk
GIT_SECONDS = 4.0         # time box for all "git log" calls together
MAX_DEPTH = 4
MAX_DIRS = 40000          # a huge tree stops the walk even before the time box
MAX_MEMORY_FOLDERS = 15
MAX_NOTES = 10
MAX_REPOS = 10
REPO_DAYS = 30
SKIP_DIRS = {"node_modules", ".git", ".cache", ".Trash", "Trash", ".trash", "__pycache__", "site-packages",
             "venv", ".venv", "target", "build", "dist", "Library", "snap", ".npm", ".cargo", ".rustup", ".local",
             ".config", ".gradle", ".m2", ".pyenv", ".nvm", ".docker", "go", "vendor", ".tox", ".mypy_cache"}
NOTE_DIRS = ("notes", "Notes", "Documents/notes", "Documents/Notes")
INDEX_CAP_BYTES = 200_000
NETWORK_FS = {"nfs", "nfs4", "cifs", "smb", "smb3", "smbfs", "afs", "9p", "ncpfs", "davfs", "glusterfs", "ceph",
              "lustre", "fuse.sshfs", "fuse.rclone", "fuse.gcsfuse", "fuse.s3fs", "fuse.davfs2", "fuse.cryfs"}


def network_mounts(mounts_file: str = "/proc/self/mounts") -> list[str]:
    """Mount points of network file systems (empty where the list cannot be read)."""
    points: list[str] = []
    try:
        with open(mounts_file, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                parts = line.split()
                if len(parts) >= 3 and parts[2] in NETWORK_FS:
                    points.append(parts[1].replace("\\040", " "))
    except OSError:
        pass
    return points


def _under(path: str, root: str) -> bool:
    return path == root or path.startswith(root.rstrip("/") + "/")


class Boundary:
    """Where the walk may go: the file system of the home folder only, and no network mount (a slow or remote
    tree would stall the time-boxed walk and could expose files from another machine)."""

    def __init__(self, home: Path, mounts: list[str] | None = None):
        self.home = str(home)
        try:
            self.dev = os.stat(home).st_dev
        except OSError:
            self.dev = None
        # a network mount that holds the home folder itself is the user's own choice
        self.mounts = [m for m in (network_mounts() if mounts is None else mounts) if not _under(self.home, m)]

    def on_network(self, path: Path | str) -> bool:
        resolved = str(Path(path).resolve())
        return any(_under(resolved, m) for m in self.mounts)

    def allows(self, path: str, dev: int | None = None) -> bool:
        if any(_under(path, m) for m in self.mounts):
            return False
        if dev is None:
            try:
                dev = os.stat(path).st_dev
            except OSError:
                return False
        return self.dev is None or dev == self.dev


@dataclass
class Discovery:
    memory_folders: list[str] = field(default_factory=list)
    notes_folders: list[str] = field(default_factory=list)
    index_files: list[str] = field(default_factory=list)
    repos: list[str] = field(default_factory=list)
    mcp_servers: list[str] = field(default_factory=list)       # enabled (registry match)
    mcp_registry: dict[str, str] = field(default_factory=dict)  # server name -> registry key
    mcp_not_used: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)              # things that stopped early

    def context_values(self) -> dict[str, list[str]]:
        """The ``[context]`` keys this discovery fills."""
        return {"memory_folders": self.memory_folders, "notes_folders": self.notes_folders,
                "index_files": self.index_files, "repos": self.repos, "mcp_servers": self.mcp_servers}

    def empty(self) -> bool:
        return not any(self.context_values().values())

    def unattended_values(self) -> tuple[dict[str, list[str]], list[str]]:
        """What may be saved when nobody confirmed (no terminal and no ``--yes``): only the recent Git repositories.
        Agent memory folders, notes vaults, the instruction and index files (your global ``CLAUDE.md`` /
        ``AGENTS.md`` and the ``MEMORY.md`` indexes) and memory MCP servers carry the most personal content, and
        an agent running setup must not decide to hand them to the relate pass. Returns ``(values, withheld)``;
        ``withheld`` names each key that had entries, with the count."""
        values = self.context_values()
        keep = ("repos",)
        withheld = [f"{k} ({len(v)})" for k, v in values.items() if k not in keep and v]
        return {k: list(v) for k, v in values.items() if k in keep}, withheld


def _tilde(path: Path | str, home: Path) -> str:
    text = str(path)
    prefix = str(home)
    return "~" + text[len(prefix):] if text == prefix or text.startswith(prefix + os.sep) else text


def _has_files(folder: Path) -> bool:
    try:
        with os.scandir(folder) as entries:
            return any(entry.is_file() for entry in entries)
    except OSError:
        return False


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _memory_folders(home: Path, env: Mapping[str, str]) -> list[Path]:
    claude = Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser()
    codex = Path(env.get("CODEX_HOME") or home / ".codex").expanduser()
    found = [d for d in sorted((claude / "projects").glob("*/memory")) if d.is_dir() and _has_files(d)]
    found.sort(key=_mtime, reverse=True)
    found = found[:MAX_MEMORY_FOLDERS]
    if (codex / "memories").is_dir() and _has_files(codex / "memories"):
        found.append(codex / "memories")
    return found


def _git_commit_times(repo: Path, email: str, deadline: float) -> tuple[float, float]:
    """(newest commit time in the last 30 days by anyone, newest by ``email``); 0 when none or git fails."""
    def newest(*extra: str) -> float:
        left = deadline - time.monotonic()
        if left <= 0:
            return 0.0
        try:
            proc = subprocess.run(["git", "-C", str(repo), "log", "-1", f"--since={REPO_DAYS}.days.ago",
                                   "--format=%ct", *extra], capture_output=True, text=True, timeout=min(left, 3.0),
                                  stdin=subprocess.DEVNULL, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
        except (OSError, subprocess.SubprocessError):
            return 0.0
        try:
            return float(proc.stdout.strip()) if proc.returncode == 0 and proc.stdout.strip() else 0.0
        except ValueError:
            return 0.0

    anyone = newest()
    return anyone, (newest(f"--author={email}") if email and anyone else 0.0)


def _git_email() -> str:
    try:
        proc = subprocess.run(["git", "config", "--get", "user.email"], capture_output=True, text=True, timeout=3,
                              stdin=subprocess.DEVNULL)
        return proc.stdout.strip() if proc.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def _walk(home: Path, seconds: float, boundary: Boundary | None = None) -> tuple[list[Path], list[Path], list[Path], bool]:
    """(Obsidian vaults, Logseq graphs, git repos, stopped early) under ``home``, depth <= MAX_DEPTH."""
    boundary = boundary or Boundary(home)
    deadline = time.monotonic() + seconds
    vaults: list[Path] = []
    graphs: list[Path] = []
    repos: list[Path] = []
    seen = 0
    queue: collections.deque[tuple[Path, int]] = collections.deque([(home, 0)])
    stopped = False
    while queue:
        if time.monotonic() > deadline or seen > MAX_DIRS:
            stopped = True
            break
        folder, depth = queue.popleft()
        seen += 1
        try:
            with os.scandir(folder) as scan:
                entries = list(scan)
        except OSError:
            continue
        names = {e.name for e in entries}
        is_repo = ".git" in names
        if ".obsidian" in names:
            vaults.append(folder)
        if "logseq" in names and (folder / "logseq" / "config.edn").is_file():
            graphs.append(folder)
        if is_repo and folder != home:  # a dotfiles repo at ~ must not hide everything below it
            repos.append(folder)
            continue  # do not walk into a repository
        if depth >= MAX_DEPTH:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False) and entry.name not in SKIP_DIRS and not entry.name.startswith(".") \
                        and boundary.allows(entry.path, entry.stat(follow_symlinks=False).st_dev):
                    queue.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return vaults, graphs, repos, stopped


def discover(home: Path | None = None, env: Mapping[str, str] | None = None, git_email: str | None = None,
             walk_seconds: float = WALK_SECONDS, git_seconds: float = GIT_SECONDS) -> Discovery:
    """Look around ``home``. Fast by design: a time-boxed walk and a handful of ``git log`` calls."""
    env = os.environ if env is None else env
    home = (home or Path.home()).resolve()
    out = Discovery()

    boundary = Boundary(home)
    memory = [m for m in _memory_folders(home, env) if not boundary.on_network(m)]
    vaults, graphs, repo_dirs, stopped = _walk(home, walk_seconds, boundary)
    if stopped:
        out.notes.append(f"the folder search stopped early ({walk_seconds:g} s time box); results may be incomplete")
    notes: list[Path] = []
    for rel in NOTE_DIRS:
        candidate = home / rel
        if candidate.is_dir() and boundary.allows(str(candidate)) and _has_files_deep(candidate):
            notes.append(candidate)
    notes += vaults + graphs
    icloud = home / "Library" / "Mobile Documents" / "iCloud~md~obsidian" / "Documents"
    if icloud.is_dir():
        notes += [d for d in sorted(icloud.iterdir()) if (d / ".obsidian").is_dir()]
    notes = list(dict.fromkeys(n.resolve() for n in notes))[:MAX_NOTES]

    out.memory_folders = [_tilde(p, home) for p in memory]
    out.notes_folders = [_tilde(p, home) for p in notes]

    claude = Path(env.get("CLAUDE_CONFIG_DIR") or home / ".claude").expanduser()
    codex = Path(env.get("CODEX_HOME") or home / ".codex").expanduser()
    index = [claude / "CLAUDE.md", codex / "AGENTS.md"] + [m / "MEMORY.md" for m in memory]
    out.index_files = [_tilde(p, home) for p in index if p.is_file() and p.stat().st_size <= INDEX_CAP_BYTES]

    email = _git_email() if git_email is None else git_email
    deadline = time.monotonic() + git_seconds
    scored = []
    for repo in repo_dirs:
        anyone, mine = _git_commit_times(repo, email, deadline)
        if anyone:
            scored.append((repo, anyone, mine))
    own = [s for s in scored if s[2]]
    pool = own if own else scored  # prefer repos with the user's own recent commits
    pool.sort(key=lambda s: s[2] or s[1], reverse=True)
    out.repos = [_tilde(r, home) for r, _, _ in pool[:MAX_REPOS]]

    servers = mcp.load_user_servers(env, home)
    known, unknown = mcp.discover_servers(servers)
    out.mcp_servers = sorted(known)
    out.mcp_registry = dict(known)
    out.mcp_not_used = unknown
    return out


def _has_files_deep(folder: Path) -> bool:
    """A notes folder counts when it holds at least one file (one level of subfolders is enough to tell)."""
    try:
        with os.scandir(folder) as scan:
            entries = list(scan)
    except OSError:
        return False
    return any(e.is_file() or (e.is_dir() and not e.name.startswith(".") and _has_files(Path(e.path))) for e in entries)


def describe(found: Discovery) -> str:
    """The grouped, human-readable list shown before the user accepts."""
    lines: list[str] = []

    def group(title: str, items: list[str], note: str = "") -> None:
        lines.append(f"{title}" + (f" ({note})" if note else "") + ":")
        lines.extend([f"  {item}" for item in items] if items else ["  (none found)"])

    group("Memory folders", found.memory_folders, "agent memory the reader's tools keep")
    group("Notes vaults and folders", found.notes_folders, "Obsidian, Logseq, ~/notes")
    group("Instruction and index files", found.index_files, "inlined into the prompt, capped")
    group("Recent repositories", found.repos, f"commits in the last {REPO_DAYS} days")
    mcp_lines = [f"{n}  ->  {', '.join(mcp.REGISTRY[found.mcp_registry[n]]['tools'])}" for n in found.mcp_servers]
    group("Memory MCP servers", mcp_lines, "read-only tools only")
    if found.mcp_not_used:
        lines.append("  not used (unknown servers are never enabled): " + ", ".join(found.mcp_not_used))
        lines.append("  to use one, " + mcp.HOW_TO_ADD)
    lines.extend(f"note: {n}" for n in found.notes)
    return "\n".join(lines)
