"""Sync bookmarks from the X API v2 (``GET /2/users/{id}/bookmarks``) into SQLite.

You need your own X API access and an OAuth 2.0 *user* token with the
``bookmark.read`` scope (an app-only bearer token cannot read bookmarks).
Only the standard library is used. The token is read from ``X_BEARER_TOKEN``, the
config file or the login saved by ``login x`` (see ``xauth``) and is never logged or stored in the database.
"""
from __future__ import annotations

import html
import http.client
import json
import re
import sqlite3
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from . import netsafe, postdata
from .store import get_meta, set_meta, timestamp

DEFAULT_API = "https://api.x.com/2"
PAGE_SIZE = 100
CHECKPOINT_KEY = "sync_checkpoint_tweet_id"
USER_ID_KEY = "x_user_id"

TWEET_FIELDS = "created_at,author_id,attachments,note_tweet,entities,public_metrics"
EXPANSIONS = "author_id,attachments.media_keys"
USER_FIELDS = "username,name,profile_image_url,verified"
MEDIA_FIELDS = "type,url,preview_image_url,alt_text,width,height,duration_ms,variants"

_ID = re.compile(r"\d{1,25}")


NO_TOKEN = ("no X token: run `unhoard login x`, or set X_BEARER_TOKEN "
            "(an OAuth 2.0 user token with bookmark.read)")


class SyncError(RuntimeError):
    """A sync could not complete (network, auth, rate limit or malformed response)."""

    def __init__(self, message: str, status: int | None = None):
        super().__init__(message)
        self.status = status  # the HTTP status of the answer, when there was one (401 means the token was refused)


Fetch = Callable[[str, str], dict]


def fetch_json(url: str, token: str, timeout: int = 30) -> dict:
    problem = netsafe.credential_url_problem(url)  # the bearer token never goes over plain http to another machine
    if problem:
        raise SyncError(problem)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    try:
        with netsafe.strict_opener().open(req, timeout=timeout) as resp:  # https API (or loopback in tests); never follows a redirect
            return json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        detail = exc.read()[:300].decode("utf-8", "replace")
        exc.close()
        if exc.code == 429:
            reset = exc.headers.get("x-rate-limit-reset", "unknown")
            raise SyncError(f"X API rate limit reached (resets at epoch {reset}); try again later") from exc
        if exc.code in (401, 403):
            raise SyncError(
                f"X API refused the token (HTTP {exc.code}). Bookmarks need an OAuth 2.0 user token "
                f"with the bookmark.read scope and an API plan that includes bookmarks. {detail}", exc.code) from exc
        raise SyncError(f"X API returned HTTP {exc.code}: {detail}", exc.code) from exc
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, http.client.HTTPException) as exc:
        raise SyncError(f"X API request failed: {exc}") from exc


def whoami(api_base: str, token: str, fetch: Fetch = fetch_json) -> str:
    user_id = str(((fetch(f"{api_base}/users/me", token) or {}).get("data") or {}).get("id") or "")
    if not _ID.fullmatch(user_id):
        raise SyncError("could not determine your X user id; set [x] user_id in the config")
    return user_id


def page_url(api_base: str, user_id: str, pagination_token: str | None) -> str:
    query = {
        "max_results": PAGE_SIZE, "tweet.fields": TWEET_FIELDS, "expansions": EXPANSIONS,
        "user.fields": USER_FIELDS, "media.fields": MEDIA_FIELDS,
    }
    if pagination_token:
        query["pagination_token"] = pagination_token
    return f"{api_base}/users/{urllib.parse.quote(user_id)}/bookmarks?{urllib.parse.urlencode(query)}"


def _store_media(db: sqlite3.Connection, tid: str, tweet: dict, medias: dict) -> None:
    """Upsert the tweet's media in attachment order (position 0, 1, ...), with size, duration and the best mp4."""
    for position, key in enumerate((tweet.get("attachments") or {}).get("media_keys") or []):
        fields = postdata.media_fields(medias.get(key) or {})
        db.execute(
            """INSERT INTO media(media_key,tweet_id,media_type,url,alt_text,position,video_url,width,height,duration_ms)
               VALUES(?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(media_key) DO UPDATE SET url=excluded.url, media_type=excluded.media_type,
                 alt_text=excluded.alt_text, position=excluded.position, video_url=excluded.video_url,
                 width=excluded.width, height=excluded.height, duration_ms=excluded.duration_ms""",
            (str(key), tid, fields["media_type"], fields["url"], fields["alt_text"], position, fields["video_url"],
             fields["width"], fields["height"], fields["duration_ms"]))


def refresh_payload(db: sqlite3.Connection, payload: dict) -> dict[str, int]:
    """Update the display fields (avatar, metrics, readable text, links, media) of bookmarks that already exist.

    Idempotent. Never inserts a bookmark and never touches ``text``, ``state``, enrichments or decisions.
    """
    includes = payload.get("includes") or {}
    users = {u.get("id"): u for u in includes.get("users") or []}
    medias = {m.get("media_key"): m for m in includes.get("media") or []}
    seen = updated = 0
    for tweet in payload.get("data") or []:
        tid = str(tweet.get("id", ""))
        if not _ID.fullmatch(tid):
            continue
        seen += 1
        if not db.execute("SELECT 1 FROM bookmarks WHERE tweet_id=?", (tid,)).fetchone():
            continue
        shown = postdata.stored_fields(tweet, users.get(tweet.get("author_id")) or {})
        db.execute("""UPDATE bookmarks SET author_avatar_url=?, author_verified=?, metrics_json=?, text_display=?,
                        links_json=? WHERE tweet_id=?""",
                   (shown["author_avatar_url"], shown["author_verified"], shown["metrics_json"],
                    shown["text_display"], shown["links_json"], tid))
        _store_media(db, tid, tweet, medias)
        updated += 1
    db.commit()
    return {"seen": seen, "updated": updated}


def refresh(db: sqlite3.Connection, *, token: str, user_id: str = "", api_base: str = DEFAULT_API,
            max_items: int = 500, fetch: Fetch = fetch_json) -> dict[str, Any]:
    """Re-walk the bookmark pages (newest first, up to ``max_items``) and refresh the display fields of the rows
    that exist. Leaves the sync checkpoint, enrichments and decisions alone."""
    if not token:
        raise SyncError(NO_TOKEN)
    user_id = user_id or get_meta(db, USER_ID_KEY) or whoami(api_base, token, fetch)
    set_meta(db, USER_ID_KEY, user_id)
    seen = updated = pages = 0
    cursor: str | None = None
    complete = False
    while True:
        payload = fetch(page_url(api_base, user_id, cursor), token)
        pages += 1
        rows = payload.get("data") or []
        room = max_items - seen
        if len(rows) > room:
            payload = {**payload, "data": rows[:max(room, 0)]}
        result = refresh_payload(db, payload)
        seen += result["seen"]
        updated += result["updated"]
        cursor = (payload.get("meta") or {}).get("next_token")
        if not cursor:
            complete = True
            break
        if seen >= max_items:
            break
    db.commit()
    return {"seen": seen, "updated": updated, "pages": pages, "complete": complete}


def ingest_payload(db: sqlite3.Connection, payload: dict, stop_at: str | None = None) -> dict[str, Any]:
    """Upsert one API page. Stops at ``stop_at`` (the known checkpoint) without ingesting it.

    Returns ``{"seen", "inserted", "hit_checkpoint"}``. Re-ingesting the same page is harmless.
    """
    includes = payload.get("includes") or {}
    users = {u.get("id"): u for u in includes.get("users") or []}
    medias = {m.get("media_key"): m for m in includes.get("media") or []}
    stamp = timestamp()
    seen = inserted = 0
    hit = False
    for tweet in payload.get("data") or []:
        tid = str(tweet.get("id", ""))
        if not _ID.fullmatch(tid):
            continue
        if stop_at and tid == stop_at:
            hit = True
            break
        author = users.get(tweet.get("author_id")) or {}
        username = author.get("username") or ""
        source = f"https://x.com/{username}/status/{tid}" if username else f"https://x.com/i/web/status/{tid}"
        text = html.unescape(((tweet.get("note_tweet") or {}).get("text")) or tweet.get("text") or "")
        known = db.execute("SELECT 1 FROM bookmarks WHERE tweet_id=?", (tid,)).fetchone()
        shown = postdata.stored_fields(tweet, author)
        db.execute(
            """INSERT INTO bookmarks(tweet_id,text,author_id,author_username,author_name,tweet_created_at,
                 first_seen_at,last_seen_at,source_url,raw_json,author_avatar_url,author_verified,metrics_json,
                 text_display,links_json)
               VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(tweet_id) DO UPDATE SET text=excluded.text, author_username=excluded.author_username,
                 author_name=excluded.author_name, last_seen_at=excluded.last_seen_at,
                 source_url=excluded.source_url, raw_json=excluded.raw_json,
                 author_avatar_url=excluded.author_avatar_url, author_verified=excluded.author_verified,
                 metrics_json=excluded.metrics_json, text_display=excluded.text_display,
                 links_json=excluded.links_json""",
            (tid, text, tweet.get("author_id"), username or None, author.get("name"), tweet.get("created_at"),
             stamp, stamp, source, json.dumps(tweet, ensure_ascii=False, sort_keys=True),
             shown["author_avatar_url"], shown["author_verified"], shown["metrics_json"], shown["text_display"],
             shown["links_json"]))
        _store_media(db, tid, tweet, medias)
        seen += 1
        inserted += 0 if known else 1
    db.commit()
    return {"seen": seen, "inserted": inserted, "hit_checkpoint": hit}


def sync(db: sqlite3.Connection, *, token: str, user_id: str = "", api_base: str = DEFAULT_API,
         max_items: int = 500, full: bool = False, fetch: Fetch = fetch_json) -> dict[str, Any]:
    """Pull bookmarks newest-first until the last known checkpoint (or ``max_items``).

    The checkpoint (newest tweet id of the last *complete* run) only advances
    when the run reached the end of the list or the old checkpoint, so a run cut
    short by ``max_items`` or an error never skips older unseen bookmarks.
    """
    if not token:
        raise SyncError(NO_TOKEN)
    user_id = user_id or get_meta(db, USER_ID_KEY) or whoami(api_base, token, fetch)
    set_meta(db, USER_ID_KEY, user_id)
    checkpoint = None if full else get_meta(db, CHECKPOINT_KEY)
    seen = inserted = pages = 0
    newest: str | None = None
    complete = False
    cursor: str | None = None
    while True:
        payload = fetch(page_url(api_base, user_id, cursor), token)
        pages += 1
        rows = payload.get("data") or []
        if newest is None and rows:
            newest = str(rows[0].get("id"))
        room = max_items - seen
        cut = len(rows) > room  # honour the cap exactly
        if cut:
            payload = {**payload, "data": rows[:room]}
        result = ingest_payload(db, payload, stop_at=checkpoint)
        seen += result["seen"]
        inserted += result["inserted"]
        if cut and not result["hit_checkpoint"]:
            break  # rows were left unread: not a complete run
        if result["hit_checkpoint"]:
            complete = True
            break
        cursor = (payload.get("meta") or {}).get("next_token")
        if not cursor:
            complete = True
            break
        if seen >= max_items:
            break
    if complete and newest:
        set_meta(db, CHECKPOINT_KEY, newest)
    db.commit()
    return {"seen": seen, "inserted": inserted, "pages": pages, "complete": complete, "truncated": not complete}
