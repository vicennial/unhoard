"""Turn the X API objects into what the card page shows: avatar, counts, media, readable text and links.

Pure functions, no I/O. ``sync`` stores the results; ``queue`` / ``server`` read them back.
"""
from __future__ import annotations

import html
import json
import re
from typing import Any, Mapping

METRIC_KEYS = (("replies", "reply_count"), ("reposts", "retweet_count"), ("quotes", "quote_count"),
               ("likes", "like_count"), ("saves", "bookmark_count"), ("views", "impression_count"))
MEDIA_TYPES = ("photo", "video", "animated_gif")
_MEDIA_LINK = re.compile(r"/(photo|video)/\d+/?$")
_PIC_HOST = re.compile(r"^(pic\.(x|twitter)\.com)/", re.I)


def https_url(value: Any) -> str | None:
    """``value`` when it is an https URL, else None (nothing else is ever rendered or fetched)."""
    return value if isinstance(value, str) and value.startswith("https://") and " " not in value else None


def avatar_url(user: Mapping[str, Any]) -> str | None:
    """The profile picture in the large size: X returns ``..._normal.jpg`` (48 px); ``_400x400`` is sharp."""
    url = https_url(user.get("profile_image_url"))
    return re.sub(r"_normal(\.[A-Za-z0-9]+)$", r"_400x400\1", url) if url else None


def metrics(public: Any) -> dict[str, int | None] | None:
    """``{replies, reposts, quotes, likes, saves, views}`` from ``public_metrics``; None when absent."""
    if not isinstance(public, dict):
        return None
    out = {}
    for name, key in METRIC_KEYS:
        value = public.get(key)
        out[name] = value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return out


def best_video_url(variants: Any) -> str | None:
    """The highest-bit-rate ``video/mp4`` variant (the HLS playlist, which has no bit rate, is skipped)."""
    best: tuple[int, str] | None = None
    for item in variants if isinstance(variants, list) else []:
        if not isinstance(item, dict) or item.get("content_type") != "video/mp4":
            continue
        url = https_url(item.get("url"))
        rate = item.get("bit_rate")
        rate = rate if isinstance(rate, int) else 0
        if url and (best is None or rate > best[0]):
            best = (rate, url)
    return best[1] if best else None


def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def media_fields(item: Mapping[str, Any]) -> dict[str, Any]:
    """The stored columns for one X media object (photo / video / animated_gif)."""
    kind = item.get("type") if item.get("type") in MEDIA_TYPES else None
    return {"media_type": kind, "url": https_url(item.get("url")) or https_url(item.get("preview_image_url")),
            "video_url": best_video_url(item.get("variants")) if kind in ("video", "animated_gif") else None,
            "width": _int(item.get("width")), "height": _int(item.get("height")),
            "duration_ms": _int(item.get("duration_ms")), "alt_text": item.get("alt_text")}


def _is_media_link(entity: Mapping[str, Any]) -> bool:
    """A link that only points at an attached photo / video: it has a media key, a ``pic.x.com`` display form or an
    expanded ``.../status/<id>/photo/1`` (or ``/video/1``) URL."""
    return bool(entity.get("media_key") or _PIC_HOST.match(str(entity.get("display_url") or ""))
                or _MEDIA_LINK.search(str(entity.get("expanded_url") or "")))


def _urls(tweet: Mapping[str, Any]) -> list[dict]:
    note = tweet.get("note_tweet") or {}
    entities = (note.get("entities") if note.get("text") else None) or tweet.get("entities") or {}
    urls = entities.get("urls") if isinstance(entities, dict) else None
    return [u for u in urls or [] if isinstance(u, dict)]


def display(tweet: Mapping[str, Any]) -> tuple[str, list[dict[str, str]]]:
    """``(text, links)``: the post text, newlines kept, HTML-unescaped, with each t.co link replaced by its display
    form and links to attached media removed; ``links`` lists the other links as ``{url, display}``."""
    note = tweet.get("note_tweet") or {}
    text = html.unescape(note.get("text") or tweet.get("text") or "")
    links: list[dict[str, str]] = []
    for entity in _urls(tweet):
        short = str(entity.get("url") or "")
        if not short:
            continue
        if _is_media_link(entity):
            text = text.replace(short, "")
            continue
        shown = str(entity.get("display_url") or entity.get("expanded_url") or short)
        text = text.replace(short, shown)
        expanded = https_url(entity.get("expanded_url")) or (
            entity["expanded_url"] if str(entity.get("expanded_url", "")).startswith("http://") else None)
        if expanded and not any(link["url"] == expanded for link in links):
            links.append({"url": expanded, "display": shown})
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"[ \t]{2,}$", "", text, flags=re.M)
    return text.strip(), links


def stored_fields(tweet: Mapping[str, Any], author: Mapping[str, Any]) -> dict[str, Any]:
    """The bookmark columns derived from a tweet and its author (the ones added in schema 3)."""
    text, links = display(tweet)
    public = tweet.get("public_metrics")
    verified = author.get("verified")
    return {"author_avatar_url": avatar_url(author),
            "author_verified": None if not isinstance(verified, bool) else int(verified),
            "metrics_json": json.dumps(public, sort_keys=True) if isinstance(public, dict) else None,
            "text_display": text, "links_json": json.dumps(links, ensure_ascii=False)}


def load_metrics(raw: str | None) -> dict[str, int | None] | None:
    try:
        return metrics(json.loads(raw)) if raw else None
    except ValueError:
        return None


def load_links(raw: str | None) -> list[dict[str, str]]:
    try:
        data = json.loads(raw) if raw else []
    except ValueError:
        return []
    return [{"url": str(d["url"]), "display": str(d.get("display") or d["url"])} for d in data
            if isinstance(d, dict) and isinstance(d.get("url"), str)] if isinstance(data, list) else []
