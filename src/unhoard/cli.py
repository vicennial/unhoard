"""Command line entry point: ``unhoard``."""
from __future__ import annotations

import argparse
import json
import os
import sys
import tomllib
from pathlib import Path
from typing import Callable, Sequence

from . import __version__
from . import queue as cards
from .config import TEMPLATE, Config, ConfigError, resolve_paths
from .launch import ADAPTERS, AUTO, make_launcher, resolve_target
from .present import card_text
from .store import connect, counts

AGENT_CHOICES = ("claude", "codex")  # presets built at run time by agents.py; "custom" leaves agent empty


def _toml_list(items: Sequence[str]) -> str:
    return "[" + ", ".join(json.dumps(i) for i in items) + "]"


def _ask(prompt: str, default: str, ask: Callable[[str], str]) -> str:
    answer = ask(f"{prompt} [{default}]: ").strip()
    return answer or default


def _interactive(args: argparse.Namespace) -> bool:
    return not args.yes and bool(args.ask or sys.stdin.isatty())


def _discover_mode(args: argparse.Namespace) -> str:
    """"yes" (--yes: save what is found), "ask" (a person can answer) or "unattended" (no terminal, no --yes)."""
    if args.yes:
        return "yes"
    return "ask" if _interactive(args) else "unattended"


def _write_config(path: Path, text: str) -> None:
    """Write the config readable by you only: it may later hold a client secret or a token."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        os.fchmod(handle.fileno(), 0o600)  # also when --force replaces a file that was created with looser rights
        handle.write(text)


def cmd_setup(args: argparse.Namespace) -> int:
    paths = resolve_paths()
    if paths.config_file.exists() and not args.force:
        print(f"Config already exists: {paths.config_file}\nUse --force to overwrite it.")
        return 1
    user_id, agent, adapter = "", "claude", AUTO
    interactive = _interactive(args)
    ask = args.ask or input
    if interactive:
        user_id = _ask("Your numeric X user id (blank = look it up later)", "", ask)
        agent = _ask(f"Agent that writes the cards ({'/'.join(AGENT_CHOICES)}/custom)", agent, ask)
        adapter = _ask(f"What Go does ({'/'.join(ADAPTERS)}; auto = Omnigent when it runs, else show the brief)",
                       adapter, ask)
    if agent not in AGENT_CHOICES:
        agent = ""  # custom: the user sets read_command and relate_command
    from . import omnigent_detect, tomledit
    adapter, omnigent_values = omnigent_detect.setup_step(adapter, interactive, ask)
    text = TEMPLATE.format(user_id=json.dumps(user_id), agent=json.dumps(agent), read_command="[]",
                           relate_command="[]", adapter=json.dumps(adapter))
    try:
        if omnigent_values:
            text = tomledit.set_strings(text, "omnigent", omnigent_values)
        paths.config_file.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        _write_config(paths.config_file, text)
        paths.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    except (OSError, ValueError) as exc:  # ValueError: a value the TOML editor refuses (for example a control character)
        print(f"error: could not write {paths.config_file}: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {paths.config_file}\nData directory: {paths.data_dir}")
    if agent:
        print(f'Agent: {agent}. Its commands are built each time it runs; check them with: unhoard doctor')
    else:
        print("Agent: custom. Set [enrich] read_command and relate_command in the config.")
    if not getattr(args, "no_discover", False):
        print("\nLooking for what you work on (memory folders, notes, repositories, memory servers)...")
        _discover_into_config(paths.config_file, _discover_mode(args), ask)
    print("\nNext: unhoard login x (or export X_BEARER_TOKEN), then: "
          "unhoard sync && unhoard enrich")
    print("Try it without X access first: unhoard demo && unhoard serve")
    print("Or see it with nothing saved on this machine: unhoard try")
    return 0


def _merge_lists(existing: list, found: list[str]) -> list[str]:
    out = [str(x) for x in existing]
    have = {str(Path(x).expanduser()) for x in out}
    for item in found:
        if str(Path(item).expanduser()) not in have:
            out.append(item)
            have.add(str(Path(item).expanduser()))
    return out


def _discover_into_config(config_file: Path, mode: str, ask: Callable[[str], str]) -> int:
    """Show what discovery found, ask once, and write the accepted result into [context].

    ``mode`` "unattended" (no terminal and no ``--yes``, for example an agent running setup) saves nothing that
    only a person should accept: agent memory folders, notes vaults, the global CLAUDE.md / AGENTS.md and memory
    index files, and memory MCP servers stay out (only recent repositories are saved).
    """
    from . import discover, tomledit
    found = discover.discover()
    print(discover.describe(found))
    if found.empty():
        print("Nothing found to add. You can fill [context] in the config by hand.")
        return 0
    values = found.context_values()
    if mode == "ask":
        answer = ask("\nUse these? [Y/n]: ").strip().lower()
        if answer.startswith("n"):
            print("Nothing written. Run `unhoard context discover` any time.")
            return 0
    elif mode == "unattended":
        values, withheld = found.unattended_values()
        if withheld:
            print(f"\nNo terminal and no --yes, so nobody confirmed. NOT saved: {', '.join(withheld)}. "
                  "Run `unhoard context discover` in a terminal (or add --yes) to review and save them.")
    try:
        existing = tomllib.loads(config_file.read_text(encoding="utf-8")).get("context", {})
        merged = {key: _merge_lists(existing.get(key, []), vals) for key, vals in values.items() if vals}
        if not merged:
            print("Nothing written to [context].")
            return 0
        tomledit.update_file(config_file, "context", merged)
    except (OSError, tomllib.TOMLDecodeError, tomledit.TomlEditError) as exc:
        print(f"Could not update {config_file}: {exc}\nAdd the lists above to [context] by hand.", file=sys.stderr)
        return 1
    print(f"Wrote {', '.join(merged)} into [context] of {config_file}. Edit them any time.")
    return 0


def cmd_context(args: argparse.Namespace, cfg: Config) -> int:
    from . import discover
    if args.dry_run:
        print(discover.describe(discover.discover()))
        return 0
    if not cfg.paths.config_file.exists():
        print("No config file yet: run `unhoard setup` first.", file=sys.stderr)
        return 1
    return _discover_into_config(cfg.paths.config_file, _discover_mode(args), args.ask or input)


def cmd_doctor(_args: argparse.Namespace, cfg: Config) -> int:
    from . import doctor
    checks = [doctor.check_launch(cfg), *doctor.run_doctor(cfg)]  # first: what Go will do
    print(doctor.render(checks))
    failed = [c for c in checks if c.status == doctor.FAIL]
    print("\n" + (f"{len(failed)} check(s) failed." if failed else "No check failed."))
    return 1 if failed else 0


def cmd_sync(args: argparse.Namespace, cfg: Config) -> int:
    from . import sync, xauth
    db = connect(cfg.paths)
    try:
        if args.from_file:
            payload = json.loads(Path(args.from_file).read_text(encoding="utf-8"))
            out = sync.refresh_payload(db, payload) if args.refresh else sync.ingest_payload(db, payload)
        elif args.refresh:
            out = sync.refresh(db, **xauth.sync_kwargs(cfg), user_id=str(cfg.get("x", "user_id", "") or ""),
                               api_base=str(cfg.get("x", "api_base")),
                               max_items=args.max_items or int(cfg.get("x", "max_items", 500)))
        else:
            out = sync.sync(db, **xauth.sync_kwargs(cfg), user_id=str(cfg.get("x", "user_id", "") or ""),
                            api_base=str(cfg.get("x", "api_base")),
                            max_items=args.max_items or int(cfg.get("x", "max_items", 500)), full=args.full)
    except sync.SyncError as exc:
        print(f"sync failed: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(json.dumps(out))
    return 0


def cmd_login(args: argparse.Namespace, cfg: Config) -> int:
    from . import xauth
    return xauth.login_command(cfg, client_id=args.client_id or "", port=args.port, timeout=args.timeout,
                               no_browser=args.no_browser, replace=args.replace)


def cmd_logout(_args: argparse.Namespace, cfg: Config) -> int:
    from . import xauth
    return xauth.run_logout(cfg)


def cmd_enrich(args: argparse.Namespace, cfg: Config) -> int:
    from . import enrich
    db = connect(cfg.paths)
    try:
        out = enrich.enrich_pending(db, cfg, limit=args.limit, retry_errors=args.retry_errors)
    except enrich.EnrichmentError as exc:
        print(f"enrich: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    for warning in out.get("warnings", []):
        print(f"WARNING: {warning}", file=sys.stderr)
    print(json.dumps(out))
    return 1 if out["errors"] and not out["enriched"] else 0


def cmd_next(args: argparse.Namespace, cfg: Config) -> int:
    db = connect(cfg.paths)
    try:
        state = cards.next_card(db, force=args.more, daily_cards=int(cfg.get("queue", "daily_cards")))
    finally:
        db.close()
    if args.json:
        print(json.dumps(state, ensure_ascii=False))
    elif state["card"] is None:
        why = "today's cards are done (use --more)" if state["reason"] == "daily_limit" else "the queue is empty"
        print(f"No card: {why}.")
    else:
        print(card_text(state["card"]))
    return 0


def cmd_decide(args: argparse.Namespace, cfg: Config) -> int:
    db = connect(cfg.paths)
    try:
        launcher = make_launcher(cfg) if args.decision == "go" else None
        out = cards.decide(db, args.card_id, args.decision, args.note or "", launch=launcher,
                           later_days=int(cfg.get("queue", "later_days")))
    except (ValueError, LookupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:
        print(f"launch failed, the card is back in the queue: {exc}", file=sys.stderr)
        return 1
    finally:
        db.close()
    print(json.dumps(out))
    return 0


def cmd_serve(args: argparse.Namespace, cfg: Config) -> int:
    from .server import serve
    try:
        serve(cfg, args.port, open_browser=not args.no_open)
    except OSError as exc:  # most often: the port is already in use
        print(f"error: cannot start the server: {exc} (try --port)", file=sys.stderr)
        return 1
    return 0


def cmd_try(args: argparse.Namespace) -> int:
    from .tryout import run_tryout
    return run_tryout(args.port, open_browser=not args.no_open)


def cmd_agents(args: argparse.Namespace, cfg: Config) -> int:
    """List the agents on the local Omnigent server and save the chosen one into the config."""
    from . import omnigent_detect as od
    found = od.detect(str(cfg.get("omnigent", "api", "") or ""))
    if not found.running:
        print(od.not_running_message(found) + " Start Omnigent and run this again.", file=sys.stderr)
        return 1
    adapter = str(cfg.get("launch", "adapter", AUTO) or AUTO)
    if args.yes or not _interactive(args):
        od.print_unattended(found, adapter=adapter)
        return 0
    agent = od.pick_agent(found, args.ask or input)
    if agent is None:
        return 0
    config_file = cfg.paths.config_file
    if not config_file.exists():
        print(f"No config file yet. Run `unhoard setup` first, or put this in {config_file}:\n"
              + od.config_lines(found.api, agent))
        return 1
    # The default adapter ("auto") already starts the chosen agent while Omnigent runs, so it is left alone. An
    # explicit "print" would ignore the pick, so it becomes "auto"; an explicit "command" or "omnigent" is kept.
    note = ""
    try:
        from . import tomledit
        text = config_file.read_text(encoding="utf-8")
        text = tomledit.set_strings(text, "omnigent", {"api": found.api, "agent_id": agent.id})
        if adapter == "print":
            text = tomledit.set_strings(text, "launch", {"adapter": AUTO})
            note = ' [launch] adapter was "print"; it is now "auto".'
        elif adapter == "command":
            note = ' [launch] adapter = "command" is kept, so Go still runs your command; set it to "auto" to use Omnigent.'
        tomledit.write_atomic(config_file, text)
    except (OSError, ValueError) as exc:  # ValueError covers TOML errors and a value that did not take
        print(f"Could not update {config_file}: {exc}\nPut this in it by hand:\n"
              + od.config_lines(found.api, agent, AUTO if adapter == "print" else ""), file=sys.stderr)
        return 1
    print(f"Saved agent {agent.name} to {config_file}.{note}"
          + ("" if adapter == "command" else " Go starts it in Omnigent while Omnigent is running."))
    return 0


def cmd_phone(args: argparse.Namespace, cfg: Config) -> int:
    from . import phone
    return phone.run_phone(cfg, port=args.port, host=args.host, yes=args.yes)


def _mcp_status(plan: dict) -> dict:
    if plan["relate_kind"] == "claude":
        return plan["mcp"].summary()
    if plan["relate_kind"] == "codex":
        return {"note": "MCP servers are not used with agent = codex in this release"}
    return {"note": "MCP tools are set up for the claude preset; a custom relate_command manages its own"}


def cmd_status(_args: argparse.Namespace, cfg: Config) -> int:
    from . import enrich, xauth
    plan = enrich.command_plan(cfg, strict=False)
    target = resolve_target(cfg)
    db = connect(cfg.paths)
    try:
        info = {"version": __version__, "data_dir": str(cfg.paths.data_dir), "config_file": str(cfg.paths.config_file),
                "config_exists": cfg.paths.config_file.exists(), **xauth.status_info(cfg),
                "agent": cfg.get("enrich", "agent", "") or "custom",
                "read_command": plan["read"], "relate_command": plan["relate"],
                "enrich_warnings": plan["warnings"],
                "context_folders": [str(f) for f in cfg.context_folders()],
                "context_in_use": {
                    "folders": [{"kind": kind, "path": str(path)} for kind, path in cfg.context_sources()],
                    "index_files": [str(p) for p in cfg.index_files()],
                    "relate_timeout_seconds": cfg.relate_timeout()},
                "mcp": _mcp_status(plan),
                "allowed_hosts": cfg.allowed_hosts(),
                "require_tailscale_identity": cfg.get("server", "require_tailscale_identity", True) is not False,
                "launch_adapter": target.configured, "launch_target": target.effective,
                "launch_target_reason": target.reason,
                "counts": counts(db), "decided_today": cards.decided_today(db), "config_problems": cfg.problems}
    finally:
        db.close()
    print(json.dumps(info, indent=2))
    return 0


def cmd_demo(_args: argparse.Namespace, cfg: Config) -> int:
    from .demo import seed
    db = connect(cfg.paths)
    try:
        print(f"Added {seed(db)} sample proposals (fictional posts) to {cfg.paths.db_file}")
    finally:
        db.close()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="unhoard",
        description="Turn saved X bookmarks into a short queue of tasks your own agent can start.")
    p.add_argument("--version", action="version", version=f"unhoard {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    s = sub.add_parser("setup", help="write a config (asks a few questions on a terminal) and look for your context")
    s.add_argument("--force", action="store_true", help="overwrite an existing config")
    s.add_argument("--yes", action="store_true", help="do not ask; use defaults and accept what is found")
    s.add_argument("--no-discover", action="store_true", help="do not look for memory folders, notes and repositories")
    s.set_defaults(func=cmd_setup, ask=None, needs_config=False)

    c = sub.add_parser("context", help="what the relate pass may use to connect a post to your work")
    csub = c.add_subparsers(dest="context_command", required=True)
    s = csub.add_parser("discover", help="find memory folders, notes, repositories and memory servers; save to [context]")
    s.add_argument("--yes", action="store_true", help="do not ask; accept what is found")
    s.add_argument("--dry-run", action="store_true", help="only show what was found")
    s.set_defaults(func=cmd_context, ask=None)

    s = sub.add_parser("sync", help="fetch bookmarks from the X API")
    s.add_argument("--full", action="store_true", help="ignore the checkpoint and re-read up to --max-items")
    s.add_argument("--max-items", type=int)
    s.add_argument("--refresh", action="store_true",
                   help="re-read the bookmark pages and update avatars, counts, media and links of bookmarks you "
                        "already have (changes no decision or enrichment)")
    s.add_argument("--from-file", metavar="JSON", help="ingest a saved X API response instead of calling the API")
    s.set_defaults(func=cmd_sync)

    s = sub.add_parser("login", help="log in to X in the browser (OAuth 2.0 + PKCE); no token to mint by hand")
    s.add_argument("service", choices=["x"])
    s.add_argument("--client-id", help="client id of your own X developer app (or X_CLIENT_ID, or [x] client_id)")
    s.add_argument("--port", type=int, default=8766,
                   help="loopback port for the redirect (default 8766); the app's callback URL must use the same")
    s.add_argument("--timeout", type=int, default=300, help="seconds to wait for the browser (default 300)")
    s.add_argument("--no-browser", action="store_true", help="only print the address; do not open a browser")
    s.add_argument("--replace", action="store_true",
                   help="sign out a saved X login (revoke it at X, delete it) without asking; needed when there is no "
                        "terminal to ask on")
    s.set_defaults(func=cmd_login)

    s = sub.add_parser("logout", help="delete the saved X login (and revoke it at X when possible)")
    s.add_argument("service", choices=["x"])
    s.set_defaults(func=cmd_logout)

    s = sub.add_parser("enrich", help="have your agent write why-saved notes and tasks")
    s.add_argument("--limit", type=int, help="process at most this many bookmarks")
    s.add_argument("--retry-errors", action="store_true", help="also retry bookmarks that failed before")
    s.set_defaults(func=cmd_enrich)

    s = sub.add_parser("next", help="show the best open card")
    s.add_argument("--more", action="store_true", help="ignore the daily limit")
    s.add_argument("--json", action="store_true")
    s.set_defaults(func=cmd_next)

    s = sub.add_parser("decide", help="answer a card")
    s.add_argument("card_id")
    s.add_argument("decision", choices=cards.DECISIONS)
    s.add_argument("--note", help="note for the agent (Go only)")
    s.set_defaults(func=cmd_decide)

    s = sub.add_parser("serve", help="serve the card page on 127.0.0.1 and open it in your browser")
    s.add_argument("--port", type=int)
    s.add_argument("--no-open", action="store_true", help="do not open a browser (it is also skipped when there is no display)")
    s.set_defaults(func=cmd_serve)

    s = sub.add_parser("try", help="see the card page with fictional sample cards, in a temporary folder; "
                                   "touches none of your own config or data")
    s.add_argument("--port", type=int, help="loopback port (default: any free port)")
    s.add_argument("--no-open", action="store_true", help="do not open a browser")
    s.set_defaults(func=cmd_try, needs_config=False)

    s = sub.add_parser("phone", help="print the steps to use the card page from your phone over Tailscale "
                                     "(changes nothing unless you pass --yes)")
    s.add_argument("--host", metavar="NAME", help="the name your phone will use (default: this machine's Tailscale name)")
    s.add_argument("--port", type=int, help="the card page port (default: [server] port)")
    s.add_argument("--yes", action="store_true", help="write [server] allowed_hosts into your config")
    s.set_defaults(func=cmd_phone)

    s = sub.add_parser("agents", help="list the agents on your local Omnigent server and pick the one Go starts")
    s.add_argument("--yes", action="store_true", help="do not ask; only list the agents and the lines to set")
    s.set_defaults(func=cmd_agents, ask=None)

    sub.add_parser("status", help="show paths, counts, the agent, the context in use and config problems"
                   ).set_defaults(func=cmd_status)
    sub.add_parser("doctor", help="start each pass with a trivial prompt and check the tools it really has "
                   "(costs a few tiny agent calls)").set_defaults(func=cmd_doctor)
    sub.add_parser("demo", help="add fictional sample cards to try the page").set_defaults(func=cmd_demo)
    return p


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if getattr(args, "needs_config", True) is False:
        return args.func(args)
    try:
        cfg = Config.load()
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return args.func(args, cfg)


if __name__ == "__main__":
    raise SystemExit(main())
