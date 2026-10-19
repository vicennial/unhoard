import helpers
import json
import unittest
from urllib.parse import parse_qs, urlparse

from unhoard import sync
from unhoard.store import get_meta, set_meta

TOKEN = "test-token-not-real"


def x_responder(pages, auth_ok=True):
    """Serve fixture pages keyed by pagination_token (None = first page)."""
    def respond(handler, _body):
        url = urlparse(handler.path)
        if handler.headers.get("Authorization") != f"Bearer {TOKEN}" or not auth_ok:
            return 401, {"title": "Unauthorized"}
        if url.path == "/2/users/me":
            return 200, {"data": {"id": "42", "username": "someone"}}
        q = parse_qs(url.query)
        return 200, pages[(q.get("pagination_token") or [None])[0]]
    return respond


PAGES = {None: helpers.fixture("bookmarks_page1.json"), "page2": helpers.fixture("bookmarks_page2.json")}


class SyncTests(helpers.TempCase):
    def run_sync(self, base, **kw):
        return sync.sync(self.db, token=TOKEN, user_id="42", api_base=base + "/2", **kw)

    def test_paginates_and_stores_everything(self):
        with helpers.fake_http(x_responder(PAGES)) as (base, reqs):
            out = self.run_sync(base)
        self.assertEqual((out["seen"], out["inserted"], out["pages"], out["complete"]), (5, 5, 2, True))
        self.assertEqual(self.db.execute("SELECT count(*) FROM bookmarks").fetchone()[0], 5)
        first = urlparse(reqs[0]["path"])
        self.assertEqual(first.path, "/2/users/42/bookmarks")
        q = parse_qs(first.query)
        self.assertEqual(q["max_results"], ["100"])
        self.assertIn("note_tweet", q["tweet.fields"][0])
        self.assertEqual(parse_qs(urlparse(reqs[1]["path"]).query)["pagination_token"], ["page2"])
        self.assertEqual(reqs[0]["headers"]["Authorization"], f"Bearer {TOKEN}")

    def test_field_mapping(self):
        with helpers.fake_http(x_responder(PAGES)) as (base, _):
            self.run_sync(base)
        row = lambda tid: self.db.execute("SELECT * FROM bookmarks WHERE tweet_id=?", (tid,)).fetchone()
        self.assertEqual(row("105")["text"], "Short post about caching & sharding")  # entities unescaped
        self.assertEqual(row("105")["source_url"], "https://x.com/ada.example/status/105")
        self.assertEqual(row("104")["text"], "The full long-form text of the post, well past the truncation point.")
        self.assertEqual(row("103")["source_url"], "https://x.com/i/web/status/103")  # author not in includes
        media = {r["media_key"]: r for r in self.db.execute("SELECT * FROM media")}
        self.assertEqual(media["m1"]["url"], "https://img.example.invalid/one.jpg")
        self.assertEqual(media["m1"]["alt_text"], "A chart of build times")
        self.assertIsNone(media["m2"]["url"])  # only https image URLs are kept

    def test_checkpoint_stops_the_next_run(self):
        with helpers.fake_http(x_responder(PAGES)) as (base, _):
            self.run_sync(base)
        self.assertEqual(get_meta(self.db, sync.CHECKPOINT_KEY), "105")
        newer = {None: helpers.fixture("bookmarks_newer.json")}
        with helpers.fake_http(x_responder(newer)) as (base, reqs):
            out = self.run_sync(base)
        self.assertEqual((out["inserted"], out["pages"], out["complete"]), (2, 1, True))
        self.assertEqual(len(reqs), 1)  # never asked for page 2
        self.assertEqual(get_meta(self.db, sync.CHECKPOINT_KEY), "107")
        self.assertEqual(self.db.execute("SELECT count(*) FROM bookmarks").fetchone()[0], 7)

    def test_full_ignores_the_checkpoint(self):
        with helpers.fake_http(x_responder(PAGES)) as (base, _):
            self.run_sync(base)
            out = self.run_sync(base, full=True)
        self.assertEqual((out["seen"], out["inserted"]), (5, 0))

    def test_resync_is_idempotent(self):
        payload = helpers.fixture("bookmarks_page1.json")
        sync.ingest_payload(self.db, payload)
        first = self.db.execute("SELECT first_seen_at FROM bookmarks WHERE tweet_id='105'").fetchone()[0]
        out = sync.ingest_payload(self.db, payload)
        self.assertEqual(out["inserted"], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM bookmarks").fetchone()[0], 3)
        self.assertEqual(self.db.execute("SELECT count(*) FROM media").fetchone()[0], 1)
        self.assertEqual(self.db.execute("SELECT first_seen_at FROM bookmarks WHERE tweet_id='105'").fetchone()[0], first)

    def test_upsert_keeps_enrichment_state(self):
        sync.ingest_payload(self.db, helpers.fixture("bookmarks_page1.json"))
        self.db.execute("UPDATE bookmarks SET state='enriched' WHERE tweet_id='105'")
        self.db.commit()
        sync.ingest_payload(self.db, helpers.fixture("bookmarks_page1.json"))
        self.assertEqual(self.db.execute("SELECT state FROM bookmarks WHERE tweet_id='105'").fetchone()[0], "enriched")

    def test_cap_truncates_and_does_not_advance_checkpoint(self):
        with helpers.fake_http(x_responder(PAGES)) as (base, _):
            out = self.run_sync(base, max_items=4)
        self.assertEqual((out["seen"], out["complete"], out["truncated"]), (4, False, True))
        self.assertIsNone(get_meta(self.db, sync.CHECKPOINT_KEY))

    def test_looks_up_user_id_when_missing(self):
        with helpers.fake_http(x_responder(PAGES)) as (base, reqs):
            sync.sync(self.db, token=TOKEN, api_base=base + "/2")
        self.assertEqual(urlparse(reqs[0]["path"]).path, "/2/users/me")
        self.assertEqual(get_meta(self.db, sync.USER_ID_KEY), "42")

    def test_auth_failure_is_explained_and_never_leaks_the_token(self):
        with helpers.fake_http(x_responder(PAGES, auth_ok=False)) as (base, _):
            with self.assertRaises(sync.SyncError) as ctx:
                self.run_sync(base)
        self.assertIn("bookmark.read", str(ctx.exception))
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_the_token_is_never_sent_over_plain_http_to_another_machine(self):
        from unittest import mock
        with mock.patch.object(sync.netsafe, "strict_opener", side_effect=AssertionError("a request was made")):
            for api in ("http://api.example.invalid/2", "http://203.0.113.9/2", "ftp://api.example.invalid/2"):
                with self.assertRaisesRegex(sync.SyncError, "refusing to send credentials"):
                    sync.fetch_json(api + "/users/me", TOKEN)
                with self.assertRaisesRegex(sync.SyncError, "refusing to send credentials"):
                    sync.sync(self.db, token=TOKEN, user_id="42", api_base=api)
            with self.assertRaises(sync.SyncError) as ctx:
                sync.sync(self.db, token=TOKEN, user_id="42", api_base="http://api.example.invalid/2")
        self.assertNotIn(TOKEN, str(ctx.exception))

    def test_the_sync_command_refuses_a_plain_http_api_base(self):
        import contextlib, io
        from unhoard import cli, config
        self.paths.config_file.parent.mkdir(parents=True, exist_ok=True)
        self.paths.config_file.write_text('[x]\napi_base = "http://api.example.invalid/2"\nuser_id = "42"\n')
        cfg = config.Config.load(self.paths, env={"X_BEARER_TOKEN": TOKEN})
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            code = cli.cmd_sync(cli.build_parser().parse_args(["sync"]), cfg)
        self.assertEqual(code, 1)
        self.assertIn("refusing to send credentials", err.getvalue())
        self.assertNotIn(TOKEN, err.getvalue())

    def test_loopback_http_is_still_allowed(self):  # the tests (and a local proxy of your own) use it
        with helpers.fake_http(x_responder(PAGES)) as (base, _):
            self.assertGreater(self.run_sync(base)["seen"], 0)

    def test_rate_limit_message(self):
        with helpers.fake_http(lambda h, b: (429, {})) as (base, _):
            with self.assertRaises(sync.SyncError) as ctx:
                self.run_sync(base)
        self.assertIn("rate limit", str(ctx.exception))

    def test_requires_a_token(self):
        with self.assertRaises(sync.SyncError):
            sync.sync(self.db, token="", user_id="42")

    def test_ignores_non_numeric_ids(self):
        out = sync.ingest_payload(self.db, {"data": [{"id": "../etc", "text": "x"}, {"id": "5", "text": "ok"}]})
        self.assertEqual(out["seen"], 1)


RICH = helpers.fixture("bookmarks_rich.json")


class RichFieldTests(helpers.TempCase):
    """Avatar, metrics, ordered media with the best mp4, readable text and links (what the card page renders)."""

    def row(self, tid):
        return self.db.execute("SELECT * FROM bookmarks WHERE tweet_id=?", (tid,)).fetchone()

    def media(self, tid):
        return [dict(r) for r in self.db.execute("SELECT * FROM media WHERE tweet_id=? ORDER BY position", (tid,))]

    def test_requested_fields_include_the_new_ones(self):
        q = parse_qs(urlparse(sync.page_url("https://api.x.com/2", "42", None)).query)
        self.assertIn("public_metrics", q["tweet.fields"][0].split(","))
        self.assertTrue({"profile_image_url", "verified"} <= set(q["user.fields"][0].split(",")))
        self.assertTrue({"width", "height", "duration_ms", "variants"} <= set(q["media.fields"][0].split(",")))
        self.assertIn("url", q["media.fields"][0].split(","))
        self.assertIn("preview_image_url", q["media.fields"][0].split(","))

    def test_avatar_is_upgraded_and_must_be_https(self):
        sync.ingest_payload(self.db, RICH)
        self.assertEqual(self.row("205")["author_avatar_url"], "https://pbs.twimg.com/profile_images/111/ada_400x400.jpg")
        self.assertEqual(self.row("205")["author_verified"], 1)
        self.assertIsNone(self.row("204")["author_avatar_url"])  # http:// is dropped
        self.assertEqual(self.row("204")["author_verified"], 0)

    def test_metrics_are_stored_as_json_and_absent_when_missing(self):
        sync.ingest_payload(self.db, RICH)
        self.assertEqual(json.loads(self.row("205")["metrics_json"])["like_count"], 99)
        self.assertIsNone(self.row("203")["metrics_json"])

    def test_media_is_in_attachment_order_with_sizes_and_the_best_mp4(self):
        sync.ingest_payload(self.db, RICH)
        photos = self.media("205")
        self.assertEqual([m["media_key"] for m in photos], ["m_photo2", "m_photo1"])  # the order of the attachments
        self.assertEqual((photos[0]["width"], photos[0]["height"]), (800, 800))
        self.assertEqual(photos[1]["alt_text"], "chart one")
        video = self.media("204")[0]
        self.assertEqual(video["media_type"], "video")
        self.assertEqual(video["url"], "https://pbs.twimg.com/ext_tw_video_thumb/9/img.jpg")  # the preview image
        self.assertEqual(video["video_url"], "https://video.twimg.com/ext_tw_video/9/vid/1280x720/high.mp4")
        self.assertEqual((video["width"], video["height"], video["duration_ms"]), (1280, 720, 31000))
        self.assertIsNone(photos[0]["video_url"])

    def test_text_display_replaces_short_links_and_drops_media_links(self):
        sync.ingest_payload(self.db, RICH)
        self.assertEqual(self.row("205")["text_display"],
                         "Thread on build caching & sharding\n\nWrite-up: example.invalid/cache-writeup and the chart")
        self.assertEqual(json.loads(self.row("205")["links_json"]),
                         [{"url": "https://example.invalid/cache-writeup", "display": "example.invalid/cache-writeup"}])
        # note_tweet text and its own entities win; HTML is unescaped; newlines stay
        self.assertEqual(self.row("204")["text_display"], "Long form: see example.invalid/demo for the demo.\nSecond line <ok>.")
        self.assertEqual(json.loads(self.row("204")["links_json"]),
                         [{"url": "https://example.invalid/demo", "display": "example.invalid/demo"}])
        self.assertEqual(self.row("203")["text_display"], "No extras at all")
        self.assertEqual(json.loads(self.row("203")["links_json"]), [])

    def test_best_video_url_ignores_playlists_and_non_https(self):
        from unhoard import postdata
        self.assertIsNone(postdata.best_video_url([{"content_type": "application/x-mpegURL", "url": "https://v/x.m3u8"}]))
        self.assertIsNone(postdata.best_video_url([{"content_type": "video/mp4", "bit_rate": 9, "url": "http://v/x.mp4"}]))
        self.assertEqual(postdata.best_video_url([{"content_type": "video/mp4", "url": "https://v/a.mp4"},
                                                  {"content_type": "video/mp4", "bit_rate": 5, "url": "https://v/b.mp4"}]),
                         "https://v/b.mp4")
        self.assertIsNone(postdata.best_video_url(None))

    def test_metrics_mapping_names(self):
        from unhoard import postdata
        self.assertEqual(postdata.metrics({"reply_count": 1, "retweet_count": 2, "quote_count": 3, "like_count": 4,
                                           "bookmark_count": 5, "impression_count": 6}),
                         {"replies": 1, "reposts": 2, "quotes": 3, "likes": 4, "saves": 5, "views": 6})
        self.assertEqual(postdata.metrics({"like_count": 4})["views"], None)
        self.assertIsNone(postdata.metrics(None))


class RefreshTests(helpers.TempCase):
    """`sync --refresh`: backfill display fields on existing rows, touching no decision or enrichment."""

    def old_style_rows(self):
        """Rows as an older release stored them: no avatar, metrics, display text or media details."""
        sync.ingest_payload(self.db, RICH)
        self.db.execute("""UPDATE bookmarks SET author_avatar_url=NULL, author_verified=NULL, metrics_json=NULL,
                           text_display=NULL, links_json=NULL""")
        self.db.execute("UPDATE media SET video_url=NULL, width=NULL, height=NULL, duration_ms=NULL, position=0")
        self.db.execute("""INSERT INTO enrichments(tweet_id,summary,why_saved,confidence,enriched_at)
                           VALUES('205','kept summary','kept why',0.7,'2026-10-02T00:00:00Z')""")
        self.db.execute("UPDATE bookmarks SET state='enriched' WHERE tweet_id='205'")
        self.db.execute("""INSERT INTO actions(action_id,tweet_id,title,brief,priority,created_at,status,decided_at,
                             launch_ref,launch_status) VALUES('BMK-00000001','205','t','b','high','2026-10-02T00:00:00Z',
                             'accepted','2026-10-03T00:00:00Z','conv_1','started')""")
        self.db.commit()

    def snapshot(self):
        return {
            "enrichments": [tuple(r) for r in self.db.execute("SELECT * FROM enrichments ORDER BY tweet_id")],
            "actions": [tuple(r) for r in self.db.execute("SELECT * FROM actions ORDER BY action_id")],
            "state": [tuple(r) for r in self.db.execute("SELECT tweet_id,text,state,first_seen_at,error FROM bookmarks ORDER BY tweet_id")],
            "meta": [tuple(r) for r in self.db.execute("SELECT * FROM meta ORDER BY key")],
        }

    def test_refresh_payload_fills_the_new_fields_and_nothing_else(self):
        self.old_style_rows()
        before = self.snapshot()
        out = sync.refresh_payload(self.db, RICH)
        self.assertEqual(out, {"seen": 3, "updated": 3})
        self.assertEqual(self.snapshot(), before)  # decisions, enrichment, text, state, checkpoint untouched
        row = self.db.execute("SELECT * FROM bookmarks WHERE tweet_id='205'").fetchone()
        self.assertEqual(row["author_avatar_url"], "https://pbs.twimg.com/profile_images/111/ada_400x400.jpg")
        self.assertIn("example.invalid/cache-writeup", row["text_display"])
        media = [r["media_key"] for r in self.db.execute("SELECT media_key FROM media WHERE tweet_id='205' ORDER BY position")]
        self.assertEqual(media, ["m_photo2", "m_photo1"])
        self.assertEqual(self.db.execute("SELECT video_url FROM media WHERE media_key='m_video'").fetchone()[0],
                         "https://video.twimg.com/ext_tw_video/9/vid/1280x720/high.mp4")

    def test_refresh_is_idempotent_and_never_inserts(self):
        self.old_style_rows()
        self.db.execute("DELETE FROM bookmarks WHERE tweet_id='203'")
        self.db.commit()
        sync.refresh_payload(self.db, RICH)
        first = [tuple(r) for r in self.db.execute("SELECT * FROM bookmarks ORDER BY tweet_id")]
        media_first = [tuple(r) for r in self.db.execute("SELECT * FROM media ORDER BY media_key")]
        out = sync.refresh_payload(self.db, RICH)
        self.assertEqual(out, {"seen": 3, "updated": 2})  # 203 is not a known bookmark: skipped, not inserted
        self.assertEqual([tuple(r) for r in self.db.execute("SELECT * FROM bookmarks ORDER BY tweet_id")], first)
        self.assertEqual([tuple(r) for r in self.db.execute("SELECT * FROM media ORDER BY media_key")], media_first)
        self.assertEqual(self.db.execute("SELECT count(*) FROM bookmarks").fetchone()[0], 2)

    def test_refresh_walks_all_pages_without_moving_the_checkpoint(self):
        self.old_style_rows()
        set_meta(self.db, sync.CHECKPOINT_KEY, "205")
        self.db.commit()
        page1 = {**RICH, "data": RICH["data"][:2], "meta": {"next_token": "p2"}}
        page2 = {"data": RICH["data"][2:], "includes": RICH["includes"], "meta": {}}
        with helpers.fake_http(x_responder({None: page1, "p2": page2})) as (base, reqs):
            out = sync.refresh(self.db, token=TOKEN, user_id="42", api_base=base + "/2")
        self.assertEqual(out, {"seen": 3, "updated": 3, "pages": 2, "complete": True})
        self.assertEqual(get_meta(self.db, sync.CHECKPOINT_KEY), "205")  # untouched
        self.assertEqual(len(reqs), 2)

    def test_refresh_respects_max_items_and_needs_a_token(self):
        self.old_style_rows()
        page1 = {**RICH, "meta": {"next_token": "p2"}}
        with helpers.fake_http(x_responder({None: page1})) as (base, reqs):
            out = sync.refresh(self.db, token=TOKEN, user_id="42", api_base=base + "/2", max_items=2)
        self.assertEqual((out["seen"], out["complete"], len(reqs)), (2, False, 1))
        with self.assertRaises(sync.SyncError):
            sync.refresh(self.db, token="", user_id="42")


if __name__ == "__main__":
    unittest.main()
