"""The first-run checklist behind ``GET /api/setup``: what is set up, and the exact next command for what is not.

Read-only. Nothing here starts an agent, calls X, or returns a secret: the answers are booleans, counts, short
fixed sentences and fixed command lines (plus the odd config value that is checked against a strict pattern first).
The one network touch is the launch item: with the default ``auto`` adapter it asks a loopback Omnigent address for
its agent list (a short GET) to tell whether Go can start a session there.
"""
from __future__ import annotations

import sqlite3

from . import xauth
from .config import ENV_TOKEN, Config
from .launch import AUTO, resolve_target

# The fictional demo cards (``unhoard demo``) all use tweet ids that start with this.
DEMO_ID_PREFIX = "9000000000000000"

SETUP = "unhoard setup"
LOGIN = "unhoard login x"
SYNC_ENRICH = "unhoard sync && unhoard enrich --limit 5"
ENRICH = "unhoard enrich --limit 5"


def x_token_source(cfg: Config) -> str | None:
    """Where the X token comes from: ``"env"``, ``"config"``, ``"login"`` (``login x``) or ``None``.

    One source of truth: ``xauth.token_source`` (the same order ``sync`` uses)."""
    return xauth.token_source(cfg)


def _item(key: str, label: str, status: str, detail: str, command: str | None = None) -> dict:
    return {"id": key, "label": label, "status": status, "detail": detail, "command": command}


def _x_access(cfg: Config) -> dict:
    source = x_token_source(cfg)
    if source is None:
        return _item("x_access", "X access", "todo",
                     f"No token. Log in, or set {ENV_TOKEN} (bookmark.read).", LOGIN)
    if source == "login":
        return _item("x_access", "X access", "ok", _login_detail(cfg))
    where = {"env": f"from {ENV_TOKEN}", "config": "from the config file"}.get(source, source)
    return _item("x_access", "X access", "ok", f"Token set, {where}.")


def _login_detail(cfg: Config) -> str:
    """"Logged in as @name; access token valid until ..." from the saved login. Never contains a token."""
    login = xauth.status_info(cfg).get("x_login") or {}
    who = f" as {login['user']}" if login.get("user") else ""
    if login.get("expired"):
        return (f"Logged in{who}; the access token has expired and refreshes on the next sync." if login.get("can_refresh")
                else f"Logged in{who}, but the access token has expired and cannot refresh. Log in again.")
    until = f"; access token valid until {login['expires_at']}" if login.get("expires_at") else ""
    return f"Logged in{who} (unhoard login x){until}."


def _user_id(cfg: Config) -> dict:
    if str(cfg.get("x", "user_id", "") or "").strip():
        return _item("user_id", "User id", "ok", "Your X user id is set.")
    if x_token_source(cfg):
        return _item("user_id", "User id", "warn", "Not set. Sync looks it up with your token.", SETUP)
    return _item("user_id", "User id", "todo", "Not set. Setup can store it.", SETUP)


def _agent(cfg: Config) -> dict:
    agent = str(cfg.get("enrich", "agent", "") or "").lower()
    if agent in ("claude", "codex"):
        return _item("agent", "Agent", "ok", f"Your {agent} agent does the enriching.")
    custom = bool(cfg.get("enrich", "read_command")) and bool(cfg.get("enrich", "relate_command"))
    if custom or cfg.get("enrich", "command"):
        return _item("agent", "Agent", "ok", "Your own commands do the enriching.")
    return _item("agent", "Agent", "todo", "None chosen: claude, codex or your own.", SETUP)


def _launch(cfg: Config) -> dict:
    target = resolve_target(cfg)
    adapter = target.configured
    if adapter == AUTO:
        if target.effective == "omnigent":
            return _item("launch", "Launch", "ok", "Omnigent found and an agent is chosen: Go starts a session there.")
        if target.omnigent_found:
            return _item("launch", "Launch", "warn", "Omnigent found, but no agent is chosen yet. Until you pick "
                         "one, Show brief only prints the brief.", "unhoard agents")
        if target.agent_set:
            return _item("launch", "Launch", "warn", "An Omnigent agent is chosen, but Omnigent does not answer on this "
                         "machine. The button says Show brief and prints the brief until it runs.")
        return _item("launch", "Launch", "info", "No Omnigent found, so the button says Show brief and prints the "
                     "brief. To run tasks in Omnigent, start it and run `unhoard agents`; or set "
                     "[launch] adapter = \"command\" to pipe the brief to your own agent.")
    if adapter == "omnigent":
        if not str(cfg.get("omnigent", "agent_id", "") or "").strip():
            return _item("launch", "Launch", "warn", "Go starts an Omnigent session; no agent picked yet.",
                         "unhoard agents")
        return _item("launch", "Launch", "ok", "Go starts an Omnigent session.")
    if adapter == "command":
        if not cfg.get("command", "argv"):
            return _item("launch", "Launch", "warn", "Go pipes the brief to a program; argv is empty.", SETUP)
        return _item("launch", "Launch", "ok", "Go pipes the brief to your command.")
    return _item("launch", "Launch", "ok", "Show brief prints the task brief (adapter = \"print\").")


def tallies(db: sqlite3.Connection) -> dict:
    """Bookmark counts, split into real ones and the fictional demo ones."""
    like = DEMO_ID_PREFIX + "%"

    def one(sql: str) -> int:
        return db.execute(sql, (like,)).fetchone()[0]

    return {
        "real_synced": one("SELECT count(*) FROM bookmarks WHERE tweet_id NOT LIKE ?"),
        "real_enriched": one("SELECT count(*) FROM bookmarks WHERE state='enriched' AND tweet_id NOT LIKE ?"),
        "demo_cards": one("SELECT count(*) FROM bookmarks WHERE tweet_id LIKE ?"),
    }


def _data(t: dict) -> dict:
    synced, enriched = t["real_synced"], t["real_enriched"]
    if not synced:
        return _item("data", "Data", "todo", "No bookmarks synced yet.", SYNC_ENRICH)
    if not enriched:
        return _item("data", "Data", "todo", f"{synced} synced, none enriched yet.", ENRICH)
    return _item("data", "Data", "ok", f"{synced} synced, {enriched} enriched.")


def next_step(items: list[dict]) -> dict | None:
    """The item to do next: the first "todo" with a command, else the first "warn" with one."""
    for status in ("todo", "warn"):
        for item in items:
            if item["status"] == status and item["command"]:
                return item
    return None


def _demo(t: dict, earlier: list[dict]) -> dict:
    if not t["demo_cards"]:
        return _item("demo", "Samples", "ok", "No sample cards. Everything is yours.")
    if not t["real_enriched"]:
        # the exact next command for the steps above that are not done
        nxt = next_step(earlier)
        nxt = nxt["command"] if nxt else None
        return _item("demo", "Samples", "warn", "You are looking at sample cards. To use your own bookmarks:", nxt)
    return _item("demo", "Samples", "ok", f"{t['demo_cards']} fictional sample cards are mixed in with yours.")


def setup_status(cfg: Config, db: sqlite3.Connection) -> dict:
    t = tallies(db)
    items = [_x_access(cfg), _user_id(cfg), _agent(cfg), _launch(cfg), _data(t)]
    items.append(_demo(t, items))
    step = next_step(items[:-1])
    for item in items:
        item["next"] = item is step
    return {
        "ok": True,
        "items": items,
        "ready": sum(1 for i in items if i["status"] in ("ok", "info")),  # "info" is a note, not a to-do
        "total": len(items),
        # the page shows the checklist by itself until a real enriched bookmark exists
        "first_run": t["real_enriched"] == 0,
        "demo": t["demo_cards"] > 0 and t["real_enriched"] == 0,
        **t,
    }
