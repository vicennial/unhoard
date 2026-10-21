import helpers
import datetime as dt
import os
import subprocess
import time
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from unhoard import discover, tomledit


def git_commit(repo: Path, email: str, days_ago: int, msg: str = "work"):
    repo.mkdir(parents=True, exist_ok=True)
    when = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%S+0000")
    env = {**os.environ, "GIT_AUTHOR_DATE": when, "GIT_COMMITTER_DATE": when, "GIT_CONFIG_GLOBAL": os.devnull,
           "GIT_CONFIG_SYSTEM": os.devnull}
    base = ["git", "-C", str(repo), "-c", "user.name=T", "-c", f"user.email={email}", "-c", "commit.gpgsign=false"]
    if not (repo / ".git").exists():
        subprocess.run(base + ["init", "-q"], check=True, capture_output=True, env=env)
    subprocess.run(base + ["commit", "-q", "--allow-empty", "-m", msg], check=True, capture_output=True, env=env)


class DiscoveryCase(helpers.TempCase):
    def setUp(self):
        super().setUp()
        self.home = self.tmp / "home"
        h = self.home
        (h / ".claude" / "projects" / "-p1" / "memory").mkdir(parents=True)
        (h / ".claude" / "projects" / "-p1" / "memory" / "MEMORY.md").write_text("- index")
        (h / ".claude" / "projects" / "-p1" / "memory" / "a.md").write_text("a")
        (h / ".claude" / "projects" / "-p2" / "memory").mkdir(parents=True)  # empty: skipped
        (h / ".claude" / "CLAUDE.md").write_text("instructions")
        (h / ".codex" / "memories").mkdir(parents=True)
        (h / ".codex" / "memories" / "m.md").write_text("m")
        (h / ".codex" / "AGENTS.md").write_text("agents")
        (h / "Vaults" / "obs" / ".obsidian").mkdir(parents=True)
        (h / "Vaults" / "obs" / "n.md").write_text("n")
        (h / "graph" / "logseq").mkdir(parents=True)
        (h / "graph" / "logseq" / "config.edn").write_text("{}")
        (h / "notes").mkdir()
        (h / "notes" / "todo.md").write_text("t")
        (h / "node_modules" / "pkg" / ".obsidian").mkdir(parents=True)  # skipped
        (h / "a" / "b" / "c" / "d" / "e" / ".obsidian").mkdir(parents=True)  # too deep
        (h / ".hidden" / "v" / ".obsidian").mkdir(parents=True)  # hidden trees are skipped
        git_commit(h / "code" / "mine", "me@example.invalid", 2)
        git_commit(h / "code" / "theirs", "other@example.invalid", 1)
        git_commit(h / "code" / "old", "me@example.invalid", 90)
        self.servers = {"gbrain": {"type": "http", "url": "https://x"}, "exa": {"type": "http", "url": "https://e"},
                        "memory": {"type": "stdio", "command": "npx", "args": ["server-memory"]}}
        (h / ".claude.json").write_text(__import__("json").dumps({"mcpServers": self.servers}))

    def run_discover(self, email="me@example.invalid", **kw):
        return discover.discover(self.home, env={}, git_email=email, **kw)


class DiscoveryTests(DiscoveryCase):
    def test_finds_every_kind(self):
        found = self.run_discover()
        self.assertEqual(found.memory_folders, ["~/.claude/projects/-p1/memory", "~/.codex/memories"])
        self.assertEqual(sorted(found.notes_folders), ["~/Vaults/obs", "~/graph", "~/notes"])
        self.assertEqual(found.index_files, ["~/.claude/CLAUDE.md", "~/.codex/AGENTS.md",
                                             "~/.claude/projects/-p1/memory/MEMORY.md"])
        self.assertEqual(found.mcp_servers, ["gbrain", "memory"])
        self.assertEqual(found.mcp_not_used, ["exa"])

    def test_one_folder_reached_by_two_names_is_listed_once(self):
        # on macOS ~/notes and ~/Notes are one folder; a link stands in for that here
        (self.home / "notes-link").symlink_to(self.home / "notes")
        paths = [self.home / "notes", self.home / "notes-link", self.home / "missing"]
        self.assertEqual(discover._distinct_folders(paths), [(self.home / "notes").resolve()])

    def test_skips_node_modules_hidden_trees_and_deep_folders(self):
        found = " ".join(self.run_discover().notes_folders)
        for bad in ("node_modules", ".hidden", "/e"):
            self.assertNotIn(bad, found)

    def test_repos_prefer_the_users_own_recent_commits(self):
        self.assertEqual(self.run_discover().repos, ["~/code/mine"])

    def test_repos_fall_back_to_any_recent_repo_when_the_email_matches_nothing(self):
        found = self.run_discover(email="nobody@example.invalid")
        self.assertEqual(found.repos, ["~/code/theirs", "~/code/mine"])  # newest first; the 90-day-old repo is out
        self.assertEqual(self.run_discover(email="").repos, ["~/code/theirs", "~/code/mine"])

    def test_repo_list_is_capped_and_newest_first(self):
        for i in range(12):
            git_commit(self.home / "many" / f"r{i}", "me@example.invalid", 3 + i)
        repos = self.run_discover().repos
        self.assertEqual(len(repos), 10)
        self.assertEqual(repos[0], "~/code/mine")
        self.assertNotIn("~/many/r11", repos)

    def test_time_box_stops_the_walk_and_says_so(self):
        found = self.run_discover(walk_seconds=0.0)
        self.assertTrue(any("stopped early" in n for n in found.notes))

    def test_a_dotfiles_repo_at_home_does_not_hide_the_rest(self):
        subprocess.run(["git", "-C", str(self.home), "init", "-q"], check=True, capture_output=True)
        found = self.run_discover()
        self.assertIn("~/Vaults/obs", found.notes_folders)
        self.assertNotIn("~", found.repos)

    def test_describe_groups_by_kind_and_explains_unknown_servers(self):
        text = discover.describe(self.run_discover())
        for heading in ("Memory folders", "Notes vaults and folders", "Instruction and index files",
                        "Recent repositories", "Memory MCP servers"):
            self.assertIn(heading, text)
        self.assertIn("gbrain  ->  query, search, get_page", text)
        self.assertIn("not used (unknown servers are never enabled): exa", text)
        self.assertIn('mcp_tools = ["mcp__<server>__<tool>"', text)

    def test_empty_home(self):
        empty = discover.discover(self.tmp / "empty-home", env={}, git_email="")
        self.assertTrue(empty.empty())

    def test_respects_claude_config_dir_and_codex_home(self):
        alt = self.tmp / "alt-claude"
        (alt / "projects" / "-x" / "memory").mkdir(parents=True)
        (alt / "projects" / "-x" / "memory" / "MEMORY.md").write_text("i")
        found = discover.discover(self.home, env={"CLAUDE_CONFIG_DIR": str(alt)}, git_email="")
        self.assertEqual(found.memory_folders[0], str(alt / "projects" / "-x" / "memory"))


class TomlEditTests(unittest.TestCase):
    TEXT = '''# my config
[enrich]
agent = "claude"

[context]
# comment kept
folders = ["~/keep"]
memory_folders = [
  "~/old",   # trailing comment
]
commands = [
  ["gbrain", "query", "{topics}"],
]
mcp_servers = []

[queue]
daily_cards = 3
'''

    def edit(self, text=None, **values):
        return tomledit.set_lists(self.TEXT if text is None else text, "context", values)

    def test_replaces_single_and_multi_line_keys_and_keeps_the_rest(self):
        out = self.edit(memory_folders=["~/a", "~/b"], mcp_servers=["gbrain"])
        data = tomllib.loads(out)
        self.assertEqual(data["context"]["memory_folders"], ["~/a", "~/b"])
        self.assertEqual(data["context"]["mcp_servers"], ["gbrain"])
        self.assertEqual(data["context"]["folders"], ["~/keep"])
        self.assertEqual(data["context"]["commands"], [["gbrain", "query", "{topics}"]])  # nested array untouched
        self.assertEqual(data["queue"]["daily_cards"], 3)
        self.assertIn("# comment kept", out)
        self.assertIn("# my config", out)

    def test_adds_missing_keys_inside_the_section(self):
        out = self.edit(notes_folders=["~/n"])
        data = tomllib.loads(out)
        self.assertEqual(data["context"]["notes_folders"], ["~/n"])
        self.assertEqual(data["queue"]["daily_cards"], 3)
        self.assertLess(out.index("notes_folders"), out.index("[queue]"))

    def test_adds_the_section_when_missing(self):
        out = tomledit.set_lists('[enrich]\nagent = "claude"\n', "context", {"repos": ["~/r"]})
        self.assertEqual(tomllib.loads(out), {"enrich": {"agent": "claude"}, "context": {"repos": ["~/r"]}})

    def test_special_characters_are_escaped(self):
        out = self.edit(folders=['~/a "quoted" \\ dir', "~/ünï"])
        self.assertEqual(tomllib.loads(out)["context"]["folders"], ['~/a "quoted" \\ dir', "~/ünï"])

    def test_invalid_input_is_not_written(self):
        with self.assertRaises(tomledit.TomlEditError):
            tomledit.set_lists("[context\nbroken", "context", {"repos": []})


class BoundaryTests(DiscoveryCase):
    def test_other_file_systems_are_not_walked(self):
        (self.home / "Mnt" / "other" / ".obsidian").mkdir(parents=True)
        (self.home / "Mnt" / "other" / "n.md").write_text("n")
        real = os.stat

        def fake_stat(path, *a, **k):
            result = real(path, *a, **k)
            if "/Mnt/" in str(path) or str(path).endswith("/Mnt"):
                return os.stat_result((result.st_mode, result.st_ino, result.st_dev + 1, *tuple(result)[3:]))
            return result

        class Entry:
            def __init__(self, entry):
                self._e, self.name, self.path = entry, entry.name, entry.path

            def is_dir(self, **k):
                return self._e.is_dir(**k)

            def is_file(self, **k):
                return self._e.is_file(**k)

            def stat(self, **k):
                return fake_stat(self.path)

        real_scandir = os.scandir

        class Scan:
            def __init__(self, path):
                self._s = real_scandir(path)

            def __enter__(self):
                return [Entry(e) for e in self._s.__enter__()]

            def __exit__(self, *a):
                return self._s.__exit__(*a)

        with mock.patch.object(discover.os, "scandir", Scan):
            found = self.run_discover()
        self.assertNotIn("~/Mnt/other", found.notes_folders)
        self.assertIn("~/Vaults/obs", found.notes_folders)  # same file system: still found

    def test_the_boundary_helper(self):
        b = discover.Boundary(self.home, mounts=["/mnt/nas"])
        self.assertFalse(b.allows("/mnt/nas/stuff", dev=b.dev))
        self.assertTrue(b.allows(str(self.home / "notes"), dev=b.dev))
        self.assertFalse(b.allows(str(self.home / "notes"), dev=(b.dev or 0) + 1))
        self.assertTrue(b.on_network("/mnt/nas/x"))
        # a network mount that holds the home folder itself is the user's own choice
        self.assertEqual(discover.Boundary(self.home, mounts=[str(self.tmp)]).mounts, [])

    def test_network_mounts_are_read_from_the_mount_table(self):
        table = self.tmp / "mounts"
        table.write_text("/dev/sda1 / ext4 rw 0 0\nnas:/vol /mnt/nas nfs4 rw 0 0\n//h/s /mnt/with\\040space cifs rw 0 0\n"
                         "sshfs#u@h: /mnt/ssh fuse.sshfs rw 0 0\ntmpfs /run tmpfs rw 0 0\n")
        self.assertEqual(discover.network_mounts(str(table)), ["/mnt/nas", "/mnt/with space", "/mnt/ssh"])
        self.assertEqual(discover.network_mounts(str(self.tmp / "missing")), [])

    def test_memory_folders_on_a_network_mount_are_dropped(self):
        orig = discover.network_mounts
        with mock.patch.object(discover, "network_mounts", lambda *a, **k: [str(self.home / ".claude")]):
            found = self.run_discover()
        self.assertEqual(found.memory_folders, ["~/.codex/memories"])
        self.assertIsNotNone(orig)

    def test_unattended_values_keep_only_repositories(self):
        found = self.run_discover()
        self.assertTrue(found.memory_folders and found.notes_folders and found.index_files and found.mcp_servers)
        values, withheld = found.unattended_values()
        self.assertEqual(values, {"repos": found.repos})
        self.assertIn("~/.claude/CLAUDE.md", found.index_files)  # found, but not saved without a person
        self.assertEqual([w.split(" ")[0] for w in withheld],
                         ["memory_folders", "notes_folders", "index_files", "mcp_servers"])
        self.assertIn(f"notes_folders ({len(found.notes_folders)})", withheld)


class TomlAtomicTests(unittest.TestCase):
    TEXT = TomlEditTests.TEXT

    def test_other_keys_are_verified_unchanged(self):
        out = tomledit.set_lists(self.TEXT, "context", {"folders": ["~/x"]})
        self.assertEqual(tomllib.loads(out)["queue"], {"daily_cards": 3})

    def test_an_edit_that_would_change_another_key_is_refused(self):
        original = tomledit._scan

        def broken(lines):  # a scanner bug: the `folders` key is thought to span the next key as well
            records = original(lines)
            ends = {r[1]: r for r in records if r[0] == "key"}
            return [("key", "folders", ends["folders"][2], ends["memory_folders"][3]) if r[:2] == ("key", "folders") else r
                    for r in records]

        with mock.patch.object(tomledit, "_scan", broken), self.assertRaises(tomledit.TomlEditError) as ctx:
            tomledit.set_lists(self.TEXT, "context", {"folders": ["~/n"]})
        self.assertIn("other than", str(ctx.exception))

    def test_update_file_writes_through_a_temp_file_and_replaces(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(self.TEXT)
            os.chmod(path, 0o600)
            seen = {}
            real_replace = os.replace

            def spy(src, dst):
                seen["src"], seen["content"] = src, Path(src).read_text()
                return real_replace(src, dst)

            with mock.patch.object(tomledit.os, "replace", spy):
                tomledit.update_file(path, "context", {"repos": ["~/r"]})
            self.assertNotEqual(seen["src"], str(path))  # never written in place
            self.assertEqual(tomllib.loads(path.read_text())["context"]["repos"], ["~/r"])
            self.assertEqual(path.read_text(), seen["content"])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["config.toml"])

    def test_a_symlinked_config_stays_a_symlink_and_the_target_is_updated(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            real_dir, link_dir = Path(tmp) / "dotfiles", Path(tmp) / "config"
            real_dir.mkdir()
            link_dir.mkdir()
            target = real_dir / "bookmarks.toml"
            target.write_text(self.TEXT)
            os.chmod(target, 0o640)
            link = link_dir / "config.toml"
            link.symlink_to(target)
            tomledit.update_file(link, "context", {"repos": ["~/r"]})
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.path.realpath(link), str(target.resolve()))
            self.assertEqual(tomllib.loads(target.read_text())["context"]["repos"], ["~/r"])
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            self.assertEqual([p.name for p in real_dir.iterdir()], ["bookmarks.toml"])  # no temp file next to it
            self.assertEqual([p.name for p in link_dir.iterdir()], ["config.toml"])  # and none beside the link
            # a link to a link
            outer = link_dir / "outer.toml"
            outer.symlink_to(link)
            tomledit.update_file(outer, "context", {"repos": ["~/s"]})
            self.assertTrue(outer.is_symlink() and link.is_symlink())
            self.assertEqual(tomllib.loads(target.read_text())["context"]["repos"], ["~/s"])

    def test_a_failed_write_leaves_the_original_and_no_temp_file(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(self.TEXT)
            with mock.patch.object(tomledit.os, "replace", side_effect=OSError("disk full")), self.assertRaises(OSError):
                tomledit.update_file(path, "context", {"repos": ["~/r"]})
            self.assertEqual(path.read_text(), self.TEXT)
            self.assertEqual([p.name for p in Path(tmp).iterdir()], ["config.toml"])

    def test_invalid_edit_is_not_written(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text("[context\nbroken")
            with self.assertRaises(tomledit.TomlEditError):
                tomledit.update_file(path, "context", {"repos": []})
            self.assertEqual(path.read_text(), "[context\nbroken")


if __name__ == "__main__":
    unittest.main()
