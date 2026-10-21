"""The public tree carries no one's private machine details: only placeholder host names, home folders and addresses.

It reads the files git tracks, so it is skipped outside a git checkout (for example in an sdist). A maintainer's own
names (hosts, ids, paths) belong in a private pre-commit hook, not here: see RELEASING.md.
"""
import helpers
import re
import shutil
import subprocess
import unittest

TAILNET_NAME = re.compile(r"\b[a-z0-9-]+\.(tail[0-9a-f]+)\.ts\.net\b")
HOME_PATH = re.compile(r"(?:/home|/Users)/([A-Za-z0-9._-]+)")
TAILSCALE_IP = re.compile(r"\b100\.(6[4-9]|[7-9][0-9]|1[01][0-9]|12[0-7])\.\d{1,3}\.\d{1,3}\b")  # 100.64.0.0/10
PLACEHOLDER_TAILNETS = {"tail1234"}
PLACEHOLDER_USERS = {"me", "you", "user", "alice", "runner"}
PLACEHOLDER_ADDRESSES = {"100.64.0.1"}


def tracked_files():
    if not shutil.which("git") or not (helpers.ROOT / ".git").exists():
        return None
    out = subprocess.run(["git", "-C", str(helpers.ROOT), "ls-files", "-z"], capture_output=True, check=False)
    if out.returncode:
        return None
    return [helpers.ROOT / name for name in out.stdout.decode().split("\0") if name]


class PublicTreeTests(unittest.TestCase):
    def test_no_real_tailnet_names_home_folders_or_tailscale_addresses(self):
        files = tracked_files()
        if files is None:
            self.skipTest("not a git checkout")
        found = []
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue  # binary or vanished
            for line_no, line in enumerate(text.splitlines(), 1):
                where = f"{path.relative_to(helpers.ROOT)}:{line_no}"
                found += [f"{where}: tailnet {m.group(0)}" for m in TAILNET_NAME.finditer(line)
                          if m.group(1) not in PLACEHOLDER_TAILNETS]
                found += [f"{where}: home folder {m.group(0)}" for m in HOME_PATH.finditer(line)
                          if m.group(1) not in PLACEHOLDER_USERS]
                found += [f"{where}: address {m.group(0)}" for m in TAILSCALE_IP.finditer(line)
                          if m.group(0) not in PLACEHOLDER_ADDRESSES]
        self.assertEqual(found, [], "use placeholders such as mybox.tail1234.ts.net and /home/me")


if __name__ == "__main__":
    unittest.main()
