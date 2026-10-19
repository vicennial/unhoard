"""Fictional sample cards, so you can try the card page without X API access or an agent."""
from __future__ import annotations

import base64
import json
import sqlite3
import urllib.parse
from importlib import resources

from .enrich import store_result
from .store import timestamp

# Everything here is FICTIONAL: names, posts, counts, pictures, links and the video address. The handles contain
# a dot, which X handles cannot (only letters, digits and underscore), so none of them can be a real account, and the
# post links point at example.com, never at X. The four portraits are AI-generated images of people who do not exist
# (made with Nano Banana Pro; package data, demo_media/av0..av3.jpg). The chart and poster
# pictures are small inline SVG images. All pictures are inlined as data: URIs, so the page works offline and
# loads nothing from the internet.
def avatar(index: int) -> str:
    """Portrait ``index`` (0..3) as a data: URI, read from the package data."""
    raw = (resources.files("unhoard") / "demo_media" / f"av{index}.jpg").read_bytes()
    return "data:image/jpeg;base64," + base64.b64encode(raw).decode("ascii")


def _svg(width: int, height: int, body: str) -> str:
    svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
           f'font-family="sans-serif">{body}</svg>')
    return "data:image/svg+xml," + urllib.parse.quote(svg)


def ci_chart(when: str, total: str, stages: list[tuple[str, int]], fill: str) -> str:
    """A square bar chart of CI stage times, like a screenshot attached to a post."""
    rows = "".join(
        f'<text x="48" y="{250 + i * 90}" font-size="26" fill="#b8b1a2">{name}</text>'
        f'<rect x="48" y="{264 + i * 90}" width="{max(8, minutes * 12)}" height="30" rx="4" fill="{fill}"/>'
        f'<text x="{60 + max(8, minutes * 12)}" y="{288 + i * 90}" font-size="24" fill="#efe9da">{minutes} min</text>'
        for i, (name, minutes) in enumerate(stages))
    return _svg(600, 600, f'<rect width="600" height="600" fill="#1f1d1a"/>'
                          f'<text x="48" y="86" font-size="28" fill="#8f897c" letter-spacing="3">CI PIPELINE · {when}</text>'
                          f'<text x="48" y="168" font-size="76" font-weight="bold" fill="#efe9da">{total}</text>{rows}')


def readme_poster() -> str:
    """A video poster: a README that leads with the result."""
    lines = [("# tinyfeed", 34, "#efe9da", "bold"), ("Reads 200 feeds into one SQLite file in 3 s.", 24, "#b8b1a2", "normal"),
             ("$ tinyfeed sync", 24, "#53d57f", "normal"), ("214 new posts · 3.1 s", 24, "#b8b1a2", "normal"),
             ("## Install", 28, "#efe9da", "bold"), ("pip install tinyfeed", 24, "#b8b1a2", "normal")]
    body = "".join(f'<text x="56" y="{92 + i * 48}" font-size="{size}" font-weight="{weight}" fill="{fill}">{text}</text>'
                   for i, (text, size, fill, weight) in enumerate(lines))
    return _svg(640, 360, f'<rect width="640" height="360" fill="#171513"/><rect x="0" y="0" width="640" height="36" fill="#262320"/>'
                          f'<circle cx="22" cy="18" r="6" fill="#565049"/><circle cx="42" cy="18" r="6" fill="#565049"/>'
                          f'<text x="320" y="24" font-size="16" fill="#8f897c" text-anchor="middle">README.md</text>{body}')


# tweet id -> (metrics, media list). Media: (type, alt text, picture, width, height, duration_ms, video_url)
SAMPLE_EXTRAS = {
    "9000000000000000001": ({"reply_count": 14, "retweet_count": 212, "quote_count": 9, "like_count": 1830,
                             "bookmark_count": 940, "impression_count": 61200},
                            [("photo", "Fictional sample photo 1: build time before",
                              ci_chart("BEFORE", "40 min", [("install", 18), ("build", 9), ("test", 13)], "#8a8170"),
                              1200, 1200, None, None),
                             ("photo", "Fictional sample photo 2: build time after",
                              ci_chart("AFTER", "6 min", [("install (cached)", 1), ("build", 2), ("test (4 shards)", 3)], "#53d57f"),
                              1200, 1200, None, None)]),
    "9000000000000000002": ({"reply_count": 3, "retweet_count": 41, "quote_count": 2, "like_count": 506,
                             "bookmark_count": 388, "impression_count": 14800}, []),
    "9000000000000000003": ({"reply_count": 27, "retweet_count": 88, "quote_count": 12, "like_count": 972,
                             "bookmark_count": 130, "impression_count": 30100},
                            [("video", "Fictional sample video: a README rewrite", readme_poster(), 1280, 720, 42000,
                              "https://video.example.invalid/fictional-sample.mp4")]),
    "9000000000000000004": ({"reply_count": 0, "retweet_count": 5, "quote_count": 0, "like_count": 64,
                             "bookmark_count": 22, "impression_count": 2100}, []),
}

# (tweet_id, handle, name, text, why_saved, confidence, [(title, brief, label, priority)])
# The order is the portrait order: av0 Maya Okafor, av1 Daniel Reyes, av2 Priya Nair, av3 Tom Becker.
SAMPLES = [
    ("9000000000000000001", "maya.okafor", "Maya Okafor",
     "We cut CI from 40 min to 6.\n\nTwo changes:\n1. cache the dependency layer\n2. shard the tests 4 ways\n\nConfig below \U0001F447",
     "Probably saved as a recipe for your own slow CI: cache what doesn't change, split the tests.", 0.8,
     [("Cache dependencies and shard this repo's tests",
       "Measure where this repo's CI time goes, cache the dependency layer and split the tests into shards, as in the "
       "post. Open a draft PR with before and after timings. Start from https://docs.example.invalid/ci/caching. "
       "Out of scope: changing the CI provider.", "work", "high")]),
    ("9000000000000000002", "priya.nair", "Priya Nair",
     "Most 'learn Rust' advice skips ownership drills. Here are 12 tiny exercises that finally made it click for me \U0001F9F5",
     "Probably saved as a study plan for a language you want to pick up.", 0.55,
     [("Turn the 12 exercises into a two-week practice plan",
       "Read the thread, group the exercises by difficulty, and write a day-by-day plan with a tiny project for each week.",
       "learning", "medium")]),
    ("9000000000000000003", "dan.reyes", "Daniel Reyes",
     "Hot take: your README is your landing page.\n\nLead with the result. Show the output. Install goes last.",
     "A reminder to tighten the README of a project you are about to share.", 0.7,
     [("Rewrite the README result-first",
       "Restructure the README: one-line result, a real output example, then install and usage. Keep claims verifiable. "
       "Checklist: www.example.invalid/readme-checklist",
       "work", "medium"),
      ("Find three READMEs that do this well",
       "Find three public READMEs that lead with the result and summarise what each does well in two lines.",
       "learning", "low")]),
    ("9000000000000000004", "tom.becker", "Tom Becker",
     "Weekend project idea: a tiny feed reader that keeps everything in one SQLite file.",
     "Looks like a fun side project rather than something urgent.", 0.4, []),
]
# Portrait index per sample: Maya av0, Daniel av1, Priya av2, Tom av3 (the SAMPLES order differs for Priya and Daniel).
PORTRAIT = {"9000000000000000001": 0, "9000000000000000003": 1, "9000000000000000002": 2, "9000000000000000004": 3}


def demo_url(tid: str) -> str:
    """The placeholder link of a demo post. It is not an X address: the people and posts do not exist."""
    return f"https://example.com/demo/{tid}"


def _decorate(db: sqlite3.Connection, tid: str, handle: str, name: str, text: str) -> None:
    """Fictional author, portrait, counts and media for a sample post.

    Also run on a demo database from an older release (it had other names and drawn initials): the author, text and
    pictures are replaced, so the page looks the same as a fresh demo. Safe to run any number of times.
    """
    public, media = SAMPLE_EXTRAS[tid]
    db.execute("""UPDATE bookmarks SET author_username=?, author_name=?, text=?, source_url=?, author_avatar_url=?,
                    author_verified=?, metrics_json=?, text_display=?, links_json='[]' WHERE tweet_id=?""",
               (handle, name, text, demo_url(tid), avatar(PORTRAIT[tid]),
                int(tid == "9000000000000000001"), json.dumps(public, sort_keys=True), text, tid))
    for position, (kind, label, picture, width, height, duration, video) in enumerate(media):
        db.execute("""INSERT OR REPLACE INTO media(media_key,tweet_id,media_type,url,alt_text,position,video_url,width,
                        height,duration_ms) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (f"demo-{tid[-1]}-{position}", tid, kind, picture, label, position, video, width, height, duration))


def seed(db: sqlite3.Connection) -> int:
    """Insert the samples (idempotent). Returns the number of proposals stored."""
    stamp = timestamp()
    stored = 0
    for tid, handle, name, text, why, conf, tasks in SAMPLES:
        db.execute("""INSERT OR IGNORE INTO bookmarks(tweet_id,text,author_id,author_username,author_name,
                        tweet_created_at,first_seen_at,last_seen_at,source_url,raw_json)
                      VALUES(?,?,?,?,?,?,?,?,?,'{}')""",
                   (tid, text, tid[-6:], handle, name, stamp, stamp, stamp, demo_url(tid)))
        _decorate(db, tid, handle, name, text)
        if db.execute("SELECT 1 FROM enrichments WHERE tweet_id=?", (tid,)).fetchone():
            continue
        stored += store_result(db, tid, {
            "summary": text[:120], "why_saved": why, "confidence": conf,
            "tasks": [{"title": t, "brief": b, "label": lab, "priority": p} for t, b, lab, p in tasks]})
    db.commit()
    return stored
