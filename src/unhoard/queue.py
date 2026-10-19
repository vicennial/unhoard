"""The card queue: which proposed task to show next, and what Go / Later / Skip do.

Ranking is priority (urgent first), then the agent's confidence about *why* the
post was saved (higher first), then the newest proposal. A small daily limit
keeps the queue a habit instead of a chore; Later hides a card for a few days.
"""
from __future__ import annotations

import datetime as dt
import re
import sqlite3
from typing import Callable

from .launch import TargetChanged
from .store import timestamp

DAILY_CARDS = 3
LATER_DAYS = 3
PRIORITY_RANK = {"urgent": 0, "high": 1, "medium": 2, "low": 3}
PRIORITIES = tuple(PRIORITY_RANK)
DECISIONS = ("go", "later", "skip")
ACTION_ID_RE = re.compile(r"^BMK-[0-9A-F]{8}$")
MAX_NOTE = 4000

Launcher = Callable[[dict, str], dict]

CARD_SQL = """
SELECT a.action_id, a.title, a.brief, a.label, a.priority, a.created_at, a.snoozed_until,
       a.status, a.decided_at, a.launch_ref, a.launch_status, a.launch_error,
       b.tweet_id, b.author_username, b.author_name, b.text, b.source_url,
       b.first_seen_at, b.tweet_created_at, b.author_avatar_url, b.author_verified, b.metrics_json, b.text_display, b.links_json,
       e.summary, e.link_summary, e.why_saved, e.confidence,
       (SELECT url FROM media m WHERE m.tweet_id=b.tweet_id AND m.url IS NOT NULL
         ORDER BY m.media_key LIMIT 1) AS media_url
FROM actions a JOIN bookmarks b USING(tweet_id) LEFT JOIN enrichments e USING(tweet_id)
"""


class NotOpenError(LookupError):
    """The card exists but is no longer an open proposal (already decided)."""


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def clean_note(value: object) -> str:
    """The user's optional free text: bounded, printable, newlines kept."""
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("note must be text")
    text = "".join(ch for ch in value.replace("\r\n", "\n") if ch in "\n\t" or ch.isprintable()).strip()
    if len(text) > MAX_NOTE:
        raise ValueError(f"note is limited to {MAX_NOTE} characters")
    return text


def decided_today(db: sqlite3.Connection, now: dt.datetime | None = None) -> int:
    """Cards answered since 00:00 UTC (Later counts; launch bookkeeping does not)."""
    day = (now or _now()).astimezone(dt.timezone.utc).strftime("%Y-%m-%d")
    return db.execute("SELECT count(*) FROM actions WHERE substr(card_decided_at,1,10)=?", (day,)).fetchone()[0]


def _age_key(stamp: str | None) -> float:
    try:
        return -dt.datetime.fromisoformat((stamp or "").replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def _rank_key(row: sqlite3.Row) -> tuple:
    return (PRIORITY_RANK.get((row["priority"] or "medium").lower(), 2),
            -(row["confidence"] or 0.0), _age_key(row["created_at"]))


def ranked(db: sqlite3.Connection, now: dt.datetime | None = None, label: str | None = None) -> list[dict]:
    """Open, un-snoozed proposals, best first."""
    rows = db.execute(CARD_SQL + " WHERE a.status='proposed' AND (a.snoozed_until IS NULL OR a.snoozed_until<=?)",
                      (timestamp(now),)).fetchall()
    rows = [r for r in rows if label is None or r["label"] == label]
    return [dict(r) for r in sorted(rows, key=_rank_key)]


def launched(db: sqlite3.Connection, limit: int = 20) -> list[dict]:
    """Cards the user answered Go, newest first: where each went (launch_ref) and how it went."""
    rows = db.execute(CARD_SQL + " WHERE a.status='accepted' ORDER BY a.decided_at DESC, a.action_id DESC LIMIT ?",
                      (int(limit),)).fetchall()
    return [dict(r) for r in rows]


def media_for(db: sqlite3.Connection, tweet_ids: list[str]) -> dict[str, list[dict]]:
    """Attached media per tweet in attachment order."""
    ids = list(dict.fromkeys(tweet_ids))
    out: dict[str, list[dict]] = {t: [] for t in ids}
    if not ids:
        return out
    marks = ",".join("?" for _ in ids)
    for row in db.execute(f"""SELECT tweet_id, media_type, url, video_url, width, height, duration_ms, alt_text
                              FROM media WHERE tweet_id IN ({marks}) ORDER BY position, rowid""", ids):
        out[row["tweet_id"]].append(dict(row))
    return out


def get_card(db: sqlite3.Connection, action_id: str) -> dict | None:
    row = db.execute(CARD_SQL + " WHERE a.action_id=?", (action_id,)).fetchone()
    return dict(row) if row else None


def next_card(db: sqlite3.Connection, *, force: bool = False, now: dt.datetime | None = None,
              daily_cards: int = DAILY_CARDS) -> dict:
    """The best open proposal, or why there is none. ``force`` ignores the daily limit."""
    now = now or _now()
    done = decided_today(db, now)
    cards = ranked(db, now)
    base = {"decided_today": done, "daily_cards": daily_cards, "waiting": len(cards)}
    if not force and done >= daily_cards:
        return {**base, "card": None, "reason": "daily_limit"}
    if not cards:
        return {**base, "card": None, "reason": "empty"}
    return {**base, "card": cards[0], "reason": None}


def board(db: sqlite3.Connection, *, now: dt.datetime | None = None, daily_cards: int = DAILY_CARDS,
          more: bool = False) -> dict:
    """What the card page shows: today's picks, the rest waiting, and how many are snoozed."""
    now = now or _now()
    done = decided_today(db, now)
    cards = ranked(db, now)
    slots = daily_cards if more else max(0, daily_cards - done)
    later = db.execute("SELECT count(*) FROM actions WHERE status='proposed' AND snoozed_until>?",
                       (timestamp(now),)).fetchone()[0]
    return {"picks": cards[:slots], "waiting": cards[slots:], "later_count": later,
            "decided_today": done, "daily_cards": daily_cards,
            "labels": sorted({c["label"] for c in cards if c["label"]})}


def decide(db: sqlite3.Connection, action_id: str, decision: str, note: str = "", launch: Launcher | None = None,
           now: dt.datetime | None = None, later_days: int = LATER_DAYS) -> dict:
    """Apply the user's answer. On Go, ``launch(card, note)`` starts the work.

    Go marks the card accepted *before* launching, so a double click cannot
    launch twice, and rolls it back to proposed if the launch fails.
    """
    if not ACTION_ID_RE.match(action_id or ""):
        raise ValueError("invalid card id")
    if decision not in DECISIONS:
        raise ValueError("decision must be go, later or skip")
    note = clean_note(note)
    now = now or _now()
    stamp = timestamp(now)
    row = db.execute("SELECT status FROM actions WHERE action_id=?", (action_id,)).fetchone()
    if row is None:
        raise LookupError("unknown card")
    if row["status"] != "proposed":
        raise NotOpenError("card was already decided")

    def update(sql: str, args: tuple) -> int:
        changed = db.execute(sql, args).rowcount
        db.commit()
        return changed

    if decision == "later":
        until = timestamp(now + dt.timedelta(days=later_days))
        if update("UPDATE actions SET snoozed_until=?, card_decided_at=? WHERE action_id=? AND status='proposed'",
                  (until, stamp, action_id)) != 1:
            raise NotOpenError("card was already decided")
        return {"ok": True, "action_id": action_id, "status": "proposed", "snoozed_until": until}
    if decision == "skip":
        if update("""UPDATE actions SET status='skipped', decided_at=?, card_decided_at=?
                     WHERE action_id=? AND status='proposed'""", (stamp, stamp, action_id)) != 1:
            raise NotOpenError("card was already decided")
        return {"ok": True, "action_id": action_id, "status": "skipped"}
    if launch is None:
        raise ValueError("Go needs a launcher")
    if update("""UPDATE actions SET status='accepted', decided_at=?, launch_status='launching', launch_error=NULL,
                 note=? WHERE action_id=? AND status='proposed'""", (stamp, note or None, action_id)) != 1:
        raise NotOpenError("card was already decided")
    try:
        result = launch(get_card(db, action_id) or {}, note)
        ref = str(result["ref"])
    except TargetChanged:
        # Nothing was started and nothing failed: the page just showed the wrong button. Leave no "launch failed"
        # trace on the card and keep the note the user typed.
        update("""UPDATE actions SET status='proposed', decided_at=NULL, launch_status=NULL, launch_error=NULL
                  WHERE action_id=? AND status='accepted' AND launch_ref IS NULL""", (action_id,))
        raise
    except Exception as exc:
        update("""UPDATE actions SET status='proposed', decided_at=NULL, launch_status='launch_failed',
                  launch_error=?, note=NULL WHERE action_id=? AND status='accepted' AND launch_ref IS NULL""",
               (str(exc)[:800], action_id))
        raise
    status = str(result.get("status") or "started")
    update("UPDATE actions SET launch_ref=?, launch_status=?, card_decided_at=? WHERE action_id=?",
           (ref, status, stamp, action_id))
    out = {"ok": True, "action_id": action_id, "status": "accepted", "launch_ref": ref, "launch_status": status}
    for key, name in (("target", "launch_target"), ("brief", "brief")):  # what ran, and the text for "Show brief"
        if isinstance(result.get(key), str):
            out[name] = result[key]
    return out
