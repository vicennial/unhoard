"""``unhoard phone``: print the steps for reaching the card page from your phone over your own private network.

It reads, and changes, nothing on its own. It looks for ``tailscale`` on PATH and asks it for this machine's name
(``tailscale status --json``, read-only). It never runs ``tailscale serve`` or ``funnel``: the commands are printed
for you to run. With ``--yes`` it writes ``[server] allowed_hosts`` into your config (the one change it can make).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import tomledit
from .config import ALLOWED_HOSTS_RULE, Config, check_allowed_host

PLACEHOLDER = "mybox.tail1234.ts.net"  # shown when this machine's name is not known; never a real name


def _clean(value: Any, limit: int = 120) -> str:
    """Text from another program, made safe to print: control and invisible characters are dropped, and it is cut short."""
    return "".join(ch for ch in str(value) if ch.isprintable())[:limit]


def find_tailscale(name: str = "tailscale") -> str | None:
    """``shutil.which`` over the PATH entries that are absolute directories. An empty entry or ``.`` means "the current
    directory", where a stray file named ``tailscale`` must not be run just because you ran this command there."""
    entries = [e for e in os.environ.get("PATH", os.defpath).split(os.pathsep) if e and os.path.isabs(e)]
    return shutil.which(name, path=os.pathsep.join(entries)) if entries else None


def tailscale_info(which: Callable[[str], str | None] | None = None, run: Callable[..., Any] | None = None) -> dict:
    """What can be learned about Tailscale here, read-only: ``installed``, ``name`` (the MagicDNS name or None),
    ``state`` (Tailscale's BackendState or None) and ``note`` (a short reason when the name could not be read)."""
    which = which or find_tailscale  # looked up at call time, so a test can stub them
    run = run or subprocess.run
    exe = which("tailscale")
    if not exe:
        return {"installed": False, "name": None, "state": None, "note": "tailscale was not found on PATH"}
    try:
        done = run([exe, "status", "--json"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"installed": True, "name": None, "state": None, "note": f"could not run tailscale status ({_clean(exc)})"}
    if done.returncode != 0:
        why = (done.stderr or done.stdout or "").strip().splitlines()
        return {"installed": True, "name": None, "state": None,
                "note": "tailscale status failed" + (f": {_clean(why[0])}" if why else "")}
    try:
        data = json.loads(done.stdout)
        name = str(((data.get("Self") or {}).get("DNSName")) or "").strip().rstrip(".").lower()
        state = _clean(data.get("BackendState") or "", 40) or None
    except (ValueError, AttributeError):
        return {"installed": True, "name": None, "state": None, "note": "tailscale status did not return JSON"}
    valid, _why = check_allowed_host(name) if name else (None, None)
    return {"installed": True, "name": valid, "state": state,
            "note": "" if valid else "tailscale did not report a MagicDNS name (is MagicDNS on, and are you logged in?)"}


def render(cfg: Config, port: int, info: dict, host: str | None) -> str:
    """The text of ``unhoard phone``. ``host`` is the name to allow (from --host, else Tailscale's), or None."""
    name = host or info.get("name")
    shown = name or PLACEHOLDER
    configured = cfg.allowed_hosts()
    lines = ["Phone access over Tailscale (your tailnet only)", ""]
    if info["installed"]:
        state = f" (state: {info['state']})" if info.get("state") else ""
        lines.append(f"Tailscale is installed{state}." + (f" This machine's name is {info['name']}." if info["name"] else
                                                          f" {info['note']}."))
    else:
        lines.append("Tailscale was not found on this machine. Install it (https://tailscale.com/download), log in with "
                     "`tailscale up`, and turn on MagicDNS and HTTPS in the Tailscale admin console. Install it on your "
                     "phone too and log in to the same tailnet.")
    if not name:
        lines.append(f"The steps below use the placeholder {PLACEHOLDER}; put this machine's own name there "
                     "(or pass it: unhoard phone --host NAME).")
    lines += ["",
              "1. Allow the name in your config (the server still listens on 127.0.0.1 only):", "",
              "     [server]", f'     allowed_hosts = ["{shown}"]', ""]
    if shown in configured:
        lines.append(f"   {shown} is already in your config.")
    else:
        lines.append("   Or let this command write it: unhoard phone --yes" + ("" if name else f" --host {PLACEHOLDER}"))
    lines += ["",
              "2. Start the card page:", "",
              f"     unhoard serve --no-open        # http://127.0.0.1:{port}", "",
              "3. Share it on your tailnet (run this yourself; unhoard never does):", "",
              f"     tailscale serve --bg {port}", "",
              "   `tailscale serve status` shows what is shared. To stop sharing: `tailscale serve reset` "
              "(that clears every Serve entry on this machine).", "",
              f"4. On your phone, with the Tailscale app connected, open  https://{shown}/", "",
              "Read this before you do it:",
              "  - Use `tailscale serve`. NEVER use `tailscale funnel`: Funnel puts the page on the public internet.",
              "  - Anyone who can reach this machine on your tailnet can open the page and press Go, and Go can start "
              "an agent that runs as you. Share the tailnet only with people you trust, or limit who may reach this "
              "machine with a Tailscale ACL.",
              "  - The page has no login of its own. Tailscale is the only gate. By that name it only answers requests "
              "that carry Tailscale's identity header, so a funnel started by mistake gets \"forbidden\" "
              "(see require_tailscale_identity in docs/phone.md before you turn that off).",
              "",
              "Without Tailscale, SSH forwarding works with no config change: from a phone SSH app, "
              f"`ssh -L {port}:127.0.0.1:{port} you@this-machine`, then open http://127.0.0.1:{port} on the phone.",
              "",
              "If you use Omnigent, its web UI can be shared over the tailnet the same way (check Omnigent's own "
              "documentation for its port and settings), so the session that Go starts opens on your phone too.",
              "",
              "More: docs/phone.md"]
    return "\n".join(lines)


class UnsafeConfig(ValueError):
    """The existing ``[server] allowed_hosts`` is not something this command can safely add to."""


def write_host(config_file: Path, host: str) -> list[str]:
    """Add ``host`` to ``[server] allowed_hosts`` (existing entries are kept), verified and written atomically.

    Refuses (``UnsafeConfig``, nothing written) unless the current value is absent or a list of valid host names:
    adding to a string would split it into letters, and a bad entry should be fixed by its owner, not carried along."""
    import tomllib
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    section = data.get("server", {})
    if not isinstance(section, dict):
        raise UnsafeConfig("[server] is not a table")
    current = section.get("allowed_hosts", [])
    if not isinstance(current, list) or not all(isinstance(h, str) for h in current):
        raise UnsafeConfig('[server] allowed_hosts must be a list of strings, for example ["mybox.tail1234.ts.net"]')
    for entry in current:
        _name, why = check_allowed_host(entry)
        if why:
            raise UnsafeConfig(f"[server] allowed_hosts has a bad entry, {_clean(entry, 80)!r}: {why}")
    merged = current if host in [check_allowed_host(h)[0] for h in current] else current + [host]
    tomledit.update_file(config_file, "server", {"allowed_hosts": merged})
    return merged


def run_phone(cfg: Config, *, port: int | None = None, host: str | None = None, yes: bool = False,
              out: Callable[[str], None] = print, info: dict | None = None) -> int:
    """The ``phone`` command. Exit status 0 on success, 1 when ``--yes`` could not write, 2 for a bad ``--host``."""
    if host is not None:
        host, why = check_allowed_host(host)
        if why:
            out(f"error: --host: {why}. It must be {ALLOWED_HOSTS_RULE}.")
            return 2
    info = info if info is not None else tailscale_info()
    shown_port = int(port or cfg.get("server", "port", 8765))
    out(render(cfg, shown_port, info, host))
    if not yes:
        return 0
    name = host or info.get("name")
    if not name:
        out("\nNot written: this machine's name is not known. Pass it with --host NAME.")
        return 1
    config_file = cfg.paths.config_file
    if not config_file.exists():
        out(f"\nNot written: there is no config file yet ({config_file}). Run: unhoard setup")
        return 1
    try:
        merged = write_host(config_file, name)
    except UnsafeConfig as exc:
        out(f"\nNot written, and nothing was changed: {exc}. Fix it by hand in {config_file}, then run this again.")
        return 1
    except (OSError, ValueError) as exc:  # ValueError covers TOML errors and an edit that did not verify
        out(f"\nNot written ({exc}). Add this by hand under [server] in {config_file}:\n  allowed_hosts = {json.dumps([name])}")
        return 1
    out(f"\nWrote [server] allowed_hosts = {json.dumps(merged)} to {config_file}. Restart `unhoard serve` to use it.")
    return 0
