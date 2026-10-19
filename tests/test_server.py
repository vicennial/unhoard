import helpers
import http.client
import json
import threading
import unittest

from unhoard import queue as cq
from unhoard.server import BookmarksServer, CSRF_HEADER
from unhoard.store import connect

HIGH = "BMK-0000000B"


class ServerCase(helpers.TempCase):
    show_media = True

    def setUp(self):
        super().setUp()
        helpers.seed_basic(self.db)
        self.db.execute("INSERT INTO media(media_key,tweet_id,media_type,url,alt_text) VALUES('m1','2','photo','https://img.example.invalid/a.jpg',NULL)")
        self.db.commit()
        self.launched = []
        self.fail_launch = False

        def launcher(card, note):
            if self.fail_launch:
                raise RuntimeError("session service unavailable")
            self.launched.append((card["action_id"], note))
            return {"ref": "conv_9", "status": "started"}

        self.server = BookmarksServer(0, lambda: connect(self.paths), launcher, 3, 3, self.show_media)
        self.server.quiet = True
        self.port = self.server.server_port
        thread = threading.Thread(target=lambda: self.server.serve_forever(poll_interval=0.02), daemon=True)
        thread.start()
        self.addCleanup(self._stop, thread)

    def _stop(self, thread):
        self.server.shutdown()
        self.server.server_close()
        thread.join(timeout=5)

    def call(self, method, path, body=None, headers=None, host=None, raw=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        self.addCleanup(conn.close)
        hdrs = dict(headers or {})
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        if data is not None:
            hdrs.setdefault("Content-Type", "application/json")
        if host:
            conn.putrequest(method, path, skip_host=True)
            conn.putheader("Host", host)
            for k, v in hdrs.items():
                conn.putheader(k, v)
            conn.putheader("Content-Length", str(len(data or b"")))
            conn.endheaders(data)
        else:
            conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        payload = resp.read()
        return resp.status, dict(resp.getheaders()), payload

    def decide(self, card_id, body, headers=None):
        hdrs = {CSRF_HEADER: "1", **(headers or {})}
        status, _, payload = self.call("POST", f"/api/cards/{card_id}/decide", body, hdrs)
        return status, json.loads(payload)


class ReadTests(ServerCase):
    def test_binds_loopback_only(self):
        self.assertEqual(self.server.server_address[0], "127.0.0.1")

    def test_serves_the_page_and_assets_with_strict_headers(self):
        status, headers, body = self.call("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"Today", body)
        csp = headers["Content-Security-Policy"]
        self.assertIn("script-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(self.call("GET", "/static/app.js")[0], 200)
        self.assertEqual(self.call("GET", "/static/app.css")[0], 200)
        self.assertEqual(self.call("GET", "/static/../server.py")[0], 404)
        self.assertEqual(self.call("GET", "/nope")[0], 404)

    def test_cards_api(self):
        status, _, payload = self.call("GET", "/api/cards")
        data = json.loads(payload)
        self.assertEqual(status, 200)
        self.assertEqual([c["action_id"] for c in data["picks"]], [HIGH, "BMK-0000000C", "BMK-0000000A"])
        self.assertEqual(data["waiting"], [])
        self.assertEqual(data["labels"], ["personal", "work"])
        card = data["picks"][0]
        self.assertEqual((card["title"], card["why_saved"], card["media_url"]),
                         ("High task", "why two", "https://img.example.invalid/a.jpg"))
        self.assertNotIn("raw_json", card)
        self.assertNotIn("launch_ref", card)

    def test_more_after_the_daily_limit(self):
        for aid in (HIGH, "BMK-0000000C", "BMK-0000000A"):
            self.assertEqual(self.decide(aid, {"decision": "skip"})[0], 200)
        data = json.loads(self.call("GET", "/api/cards")[2])
        self.assertEqual((data["picks"], data["decided_today"]), ([], 3))
        self.db.execute("INSERT INTO actions(action_id,tweet_id,title,brief,created_at) "
                        "VALUES('BMK-0000000E','1','Extra','b','2026-10-14T00:00:00Z')")
        self.db.commit()
        self.assertEqual(len(json.loads(self.call("GET", "/api/cards?more=1")[2])["picks"]), 1)

    def test_foreign_host_is_refused(self):
        self.assertEqual(self.call("GET", "/api/cards", host="evil.example:80")[0], 403)
        self.assertEqual(self.call("GET", "/", host="rebind.example")[0], 403)

    def test_foreign_origin_is_refused_on_get(self):
        self.assertEqual(self.call("GET", "/api/cards", headers={"Origin": "https://evil.example"})[0], 403)


class RichApiTests(ServerCase):
    """Avatar, metrics, ordered media, readable text, links, relate_urls, launched."""

    def setUp(self):
        super().setUp()
        from unhoard import sync
        sync.ingest_payload(self.db, helpers.fixture("bookmarks_rich.json"))
        self.db.execute("""INSERT INTO enrichments(tweet_id,summary,why_saved,confidence,enriched_at)
                           VALUES('205','s','Probably for the cache. See https://evil.example/leak?d=1 or www.foo.test.',0.99,'2026-10-02')""")
        self.db.execute("""INSERT INTO actions(action_id,tweet_id,title,brief,priority,created_at)
                           VALUES('BMK-00000205','205','Read github.com/a/b','Open https://x.example/y.',
                                  'urgent','2026-10-02T00:00:00Z')""")
        self.db.commit()

    def cards(self):
        return json.loads(self.call("GET", "/api/cards?more=1")[2])

    def card(self, aid="BMK-00000205"):
        return next(c for c in self.cards()["picks"] if c["action_id"] == aid)

    def test_every_card_has_the_post_fields(self):
        card = self.card()
        self.assertEqual(card["author_avatar_url"], "https://pbs.twimg.com/profile_images/111/ada_400x400.jpg")
        self.assertEqual(card["metrics"], {"replies": 3, "reposts": 12, "quotes": 1, "likes": 99, "saves": 40, "views": 5000})
        self.assertEqual([m["url"] for m in card["media"]],
                         ["https://pbs.twimg.com/media/two.jpg", "https://pbs.twimg.com/media/one.jpg"])
        self.assertEqual(card["media"][0], {"type": "photo", "url": "https://pbs.twimg.com/media/two.jpg", "video_url": None,
                                            "width": 800, "height": 800, "duration_ms": None, "alt_text": None})
        self.assertEqual(card["text_display"],
                         "Thread on build caching & sharding\n\nWrite-up: example.invalid/cache-writeup and the chart")
        self.assertEqual(card["links"], [{"url": "https://example.invalid/cache-writeup", "display": "example.invalid/cache-writeup"}])
        self.assertIs(card["author_verified"], True)
        self.assertIs(self.card("BMK-0000000B")["author_verified"], False)  # unknown reads as not verified
        for key in ("author_avatar_url", "author_verified", "metrics", "media", "media_url", "text_display", "links", "relate_urls"):
            self.assertIn(key, self.card("BMK-0000000B"))  # old rows get the keys too
        old = self.card("BMK-0000000B")
        self.assertEqual((old["metrics"], old["author_avatar_url"], old["links"], old["text_display"]),
                         (None, None, [], "post two"))
        self.assertEqual(old["media"][0]["url"], "https://img.example.invalid/a.jpg")  # media_url compatibility kept
        self.assertEqual(old["media_url"], "https://img.example.invalid/a.jpg")

    def test_video_entry_has_preview_and_mp4(self):
        self.db.execute("""INSERT INTO actions(action_id,tweet_id,title,brief,priority,created_at)
                           VALUES('BMK-00000204','204','Video','b','high','2026-10-02T00:00:00Z')""")
        self.db.commit()
        video = self.card("BMK-00000204")["media"][0]
        self.assertEqual((video["type"], video["url"], video["video_url"], video["duration_ms"]),
                         ("video", "https://pbs.twimg.com/ext_tw_video_thumb/9/img.jpg",
                          "https://video.twimg.com/ext_tw_video/9/vid/1280x720/high.mp4", 31000))

    def test_relate_urls_lists_urls_in_the_agent_written_text(self):
        card = self.card()
        self.assertEqual(card["relate_urls"], ["github.com/a/b", "https://x.example/y", "https://evil.example/leak?d=1", "www.foo.test"])
        self.assertEqual(self.card("BMK-0000000B")["relate_urls"], [])

    def test_relate_urls_finds_more_kinds_keeps_the_scheme_and_counts_the_rest(self):
        from unhoard import server
        text = ("ftp://files.example/x.tgz file:///etc/passwd git@host.example:org/repo.git example.com foo.co.uk "
                "www.a.test/p (see https://b.example/p). javascript:alert(1) https://en.wiki.example/A_(b) README.md install.sh")
        found = server.relate_urls({"brief": text})
        self.assertEqual(found, ["ftp://files.example/x.tgz", "file:///etc/passwd", "git@host.example:org/repo.git",
                                 "example.com", "foo.co.uk", "www.a.test/p", "https://b.example/p", "javascript:alert(1)",
                                 "https://en.wiki.example/A_(b)"])  # file names are not domains
        many = " ".join(f"https://h{i}.example/p" for i in range(13))
        card = server.public_card({"brief": many}, True)
        self.assertEqual((len(card["relate_urls"]), card["relate_urls_more"]), (10, 3))
        self.assertEqual(server.public_card({"brief": "no links"}, True)["relate_urls_more"], 0)

    def test_url_detection_is_fast_on_adversarial_text(self):
        from unhoard import server
        for text in ("a." * 1000, "a+" * 1000 + "@", "a.a" * 700, "a-" * 1000 + ".", "a." * 400 + "@", "a:" * 1000,
                     "a.a." * 500 + "com", "x" * 2000):
            best = min(self._time(server.relate_urls, {"brief": text}) for _ in range(3))
            self.assertLess(best, 0.05, f"{text[:8]!r} took {best * 1000:.1f} ms")
        card = {"title": "a." * 1000, "brief": "a+" * 1000 + "@", "why_saved": "a.a" * 700}
        best = min(self._time(server.public_card, card, True) for _ in range(3))
        self.assertLess(best, 0.05)

    @staticmethod
    def _time(fn, *args):
        import time
        start = time.perf_counter()
        fn(*args)
        return time.perf_counter() - start

    def test_hiding_media_hides_avatar_and_pictures_too(self):
        self.server.show_media = False
        card = self.card()
        self.assertEqual((card["author_avatar_url"], card["media"], card["media_url"]), (None, [], None))
        self.assertIsNotNone(card["metrics"])  # numbers are not images

    def test_unsafe_urls_are_never_passed_on(self):
        self.db.execute("UPDATE bookmarks SET author_avatar_url='javascript:alert(1)' WHERE tweet_id='205'")
        self.db.execute("UPDATE media SET url='http://insecure.example/x.jpg', video_url='http://insecure.example/v.mp4' "
                        "WHERE media_key='m_photo1'")
        self.db.commit()
        card = self.card()
        self.assertIsNone(card["author_avatar_url"])
        self.assertEqual([m["url"] for m in card["media"]], ["https://pbs.twimg.com/media/two.jpg"])

    def test_launched_lists_accepted_cards_newest_first_limited_to_20(self):
        self.assertEqual(self.cards()["launched"], [])
        status, _ = self.decide(HIGH, {"decision": "go", "note": "n"})
        self.assertEqual(status, 200)
        for i in range(22):
            self.db.execute("""INSERT INTO actions(action_id,tweet_id,title,brief,created_at,status,decided_at,launch_ref,
                                 launch_status) VALUES(?,'1','Old %d','b','2026-10-01T00:00:00Z','accepted',?,?,'started')"""
                            % i, (f"BMK-{0x10000000 + i:08X}", f"2026-10-01T00:00:{i:02d}Z", f"conv_{i}"))
        self.db.commit()
        launched = self.cards()["launched"]
        self.assertEqual(len(launched), 20)
        self.assertEqual(launched[0]["action_id"], HIGH)  # decided just now: newest
        self.assertEqual((launched[0]["launch_ref"], launched[0]["launch_status"]), ("conv_9", "started"))
        for key in ("title", "author_username", "author_name", "label", "priority", "launch_error", "author_avatar_url",
                    "metrics", "media", "text_display", "links", "relate_urls"):
            self.assertIn(key, launched[0])
        stamps = [c["decided_at"] for c in launched]
        self.assertEqual(stamps, sorted(stamps, reverse=True))
        self.assertTrue(all(c["launch_ref"] for c in launched))

    def test_csp_allows_x_video_and_images_but_nothing_else(self):
        csp = self.call("GET", "/")[1]["Content-Security-Policy"]
        directives = dict(d.strip().split(" ", 1) for d in csp.split(";") if d.strip())
        self.assertEqual(directives["media-src"], "https://video.twimg.com")
        self.assertEqual(directives["img-src"], "https: data:")
        self.assertEqual(directives["default-src"], "'none'")
        self.assertEqual(directives["connect-src"], "'self'")  # the mp4 is a media element, not fetch()
        self.assertEqual(directives["script-src"], "'self'")


class NoMediaTests(ServerCase):
    show_media = False

    def test_media_can_be_hidden(self):
        card = json.loads(self.call("GET", "/api/cards")[2])["picks"][0]
        self.assertIsNone(card["media_url"])


class DecideTests(ServerCase):
    def test_csrf_header_required(self):
        status, _, _ = self.call("POST", f"/api/cards/{HIGH}/decide", {"decision": "skip"})
        self.assertEqual(status, 403)
        self.assertEqual(self.call("POST", f"/api/cards/{HIGH}/decide", {"decision": "skip"},
                                   {CSRF_HEADER: "0"})[0], 403)
        self.assertEqual(self.db.execute("SELECT status FROM actions WHERE action_id=?", (HIGH,)).fetchone()[0], "proposed")

    def test_foreign_origin_is_rejected_even_with_the_header(self):
        status, body = self.decide(HIGH, {"decision": "skip"}, {"Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        status, body = self.decide(HIGH, {"decision": "skip"}, {"Origin": "null"})
        self.assertEqual(status, 403)
        self.assertEqual(self.db.execute("SELECT status FROM actions WHERE action_id=?", (HIGH,)).fetchone()[0], "proposed")

    def test_same_origin_is_accepted(self):
        status, body = self.decide(HIGH, {"decision": "later"}, {"Origin": f"http://127.0.0.1:{self.port}"})
        self.assertEqual((status, body["status"]), (200, "proposed"))
        self.assertIsNotNone(self.db.execute("SELECT snoozed_until FROM actions WHERE action_id=?", (HIGH,)).fetchone()[0])

    def test_foreign_host_is_rejected_on_post(self):
        status, _, _ = self.call("POST", f"/api/cards/{HIGH}/decide", {"decision": "skip"}, {CSRF_HEADER: "1"},
                                 host="evil.example")
        self.assertEqual(status, 403)

    def test_go_launches_with_the_note(self):
        status, body = self.decide(HIGH, {"decision": "go", "note": "use the free tier"})
        self.assertEqual((status, body["status"], body["launch_ref"]), (200, "accepted", "conv_9"))
        self.assertEqual(self.launched, [(HIGH, "use the free tier")])

    def test_go_failure_returns_502_and_card_stays_open(self):
        self.fail_launch = True
        status, body = self.decide(HIGH, {"decision": "go"})
        self.assertEqual(status, 502)
        self.assertIn("unavailable", body["message"])
        self.assertEqual(self.db.execute("SELECT status FROM actions WHERE action_id=?", (HIGH,)).fetchone()[0], "proposed")
        cards = json.loads(self.call("GET", "/api/cards")[2])["picks"]
        self.assertEqual(cards[0]["launch_error"], "session service unavailable")

    def test_double_decide_conflicts(self):
        self.assertEqual(self.decide(HIGH, {"decision": "skip"})[0], 200)
        self.assertEqual(self.decide(HIGH, {"decision": "go"})[0], 409)
        self.assertEqual(self.launched, [])

    def test_bad_requests(self):
        self.assertEqual(self.decide(HIGH, {"decision": "maybe"})[0], 400)
        self.assertEqual(self.decide(HIGH, {})[0], 400)
        self.assertEqual(self.decide("BMK-0000000F", {"decision": "skip"})[0], 404)
        self.assertEqual(self.call("POST", "/api/cards/not-an-id/decide", {"decision": "skip"}, {CSRF_HEADER: "1"})[0], 404)
        self.assertEqual(self.call("POST", f"/api/cards/{HIGH}/decide", None, {CSRF_HEADER: "1"}, raw=b"{bad")[0], 400)
        self.assertEqual(self.call("POST", f"/api/cards/{HIGH}/decide", None, {CSRF_HEADER: "1"}, raw=b"[1]")[0], 400)
        self.assertEqual(self.call("POST", f"/api/cards/{HIGH}/decide", None,
                                   {CSRF_HEADER: "1", "Content-Type": "text/plain"}, raw=b"{}")[0], 400)
        big = json.dumps({"decision": "skip", "note": "x" * 20000}).encode()
        self.assertEqual(self.call("POST", f"/api/cards/{HIGH}/decide", None, {CSRF_HEADER: "1"}, raw=big)[0], 400)
        self.assertEqual(self.decide(HIGH, {"decision": "go", "note": "x" * 5000})[0], 400)
        self.assertEqual(self.call("POST", "/api/cards", {"decision": "skip"}, {CSRF_HEADER: "1"})[0], 404)


if __name__ == "__main__":
    unittest.main()
