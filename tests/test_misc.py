import helpers
import ast
import base64
import asyncio
import importlib.util
import json
import os
import re
import sys
import tomllib
import unittest
from pathlib import Path
from unittest import mock

from unhoard import demo, queue as cq
from unhoard.present import card_text

SRC = helpers.ROOT / "src" / "unhoard"
EXT = helpers.ROOT / "src" / "unhoard_ext"
HAS_OMNIGENT_EXT = importlib.util.find_spec("omnigent") is not None and importlib.util.find_spec("omnigent.extensions") is not None


class DemoAndCardTextTests(helpers.TempCase):
    def test_demo_seed_is_idempotent_and_ranked(self):
        self.assertEqual(demo.seed(self.db), 4)
        self.assertEqual(demo.seed(self.db), 0)
        state = cq.next_card(self.db)
        self.assertEqual(state["card"]["priority"], "high")
        self.assertEqual(state["waiting"], 4)

    def test_demo_cards_carry_fictional_avatars_metrics_and_media(self):
        from unhoard import queue
        demo.seed(self.db)
        demo.seed(self.db)  # idempotent: no duplicate media
        rows = {r["tweet_id"]: r for r in self.db.execute("SELECT * FROM bookmarks")}
        for row in rows.values():
            self.assertTrue(row["author_avatar_url"].startswith("data:image/jpeg;base64,"))  # no network, no real person
            self.assertEqual(json.loads(row["links_json"]), [])
            self.assertIn("like_count", json.loads(row["metrics_json"]))
        media = queue.media_for(self.db, list(rows))
        two = media["9000000000000000001"]
        self.assertEqual([m["media_type"] for m in two], ["photo", "photo"])
        self.assertTrue(all("Fictional sample photo" in m["alt_text"] for m in two))
        video = media["9000000000000000003"]
        self.assertEqual([m["media_type"] for m in video], ["video"])
        self.assertEqual(video[0]["video_url"], "https://video.example.invalid/fictional-sample.mp4")
        self.assertIn("Fictional sample video", video[0]["alt_text"])
        self.assertEqual(media["9000000000000000002"], [])
        self.assertTrue(all(m["url"].startswith("data:image/svg+xml,") for items in media.values() for m in items))

    def test_demo_people_are_the_fictional_ones_with_their_own_portraits(self):
        demo.seed(self.db)
        rows = {r["author_username"]: r for r in self.db.execute("SELECT * FROM bookmarks")}
        self.assertEqual({h: r["author_name"] for h, r in rows.items()},
                         {"maya.okafor": "Maya Okafor", "dan.reyes": "Daniel Reyes",
                          "priya.nair": "Priya Nair", "tom.becker": "Tom Becker"})
        portraits = {h: r["author_avatar_url"] for h, r in rows.items()}
        self.assertEqual(len(set(portraits.values())), 4)  # four different pictures
        for index, handle in enumerate(("maya.okafor", "dan.reyes", "priya.nair", "tom.becker")):
            raw = base64.b64decode(portraits[handle].split(",", 1)[1])
            self.assertEqual(raw[:3], b"\xff\xd8\xff", handle)  # a real JPEG
            self.assertEqual(raw, (SRC / "demo_media" / f"av{index}.jpg").read_bytes(), handle)
        self.assertEqual([bool(r["author_verified"]) for h, r in rows.items()],
                         [h == "maya.okafor" for h in rows])
        self.assertTrue(rows["maya.okafor"]["text"].startswith("We cut CI from 40 min to 6.\n\nTwo changes:"))
        tasks = {r["title"] for r in self.db.execute("SELECT title FROM actions")}
        self.assertIn("Cache dependencies and shard this repo's tests", tasks)
        self.assertEqual(len(tasks), 4)  # 1 + 1 + 2 + 0 tasks over the four cards

    def test_demo_handles_cannot_be_real_x_accounts_and_links_do_not_point_at_x(self):
        demo.seed(self.db)
        for row in self.db.execute("SELECT author_username, source_url FROM bookmarks"):
            self.assertIn(".", row["author_username"])  # X handles allow only letters, digits and underscore
            self.assertTrue(row["source_url"].startswith("https://example.com/demo/"), row["source_url"])
            self.assertNotIn("x.com", row["source_url"])

    def test_a_demo_database_from_an_older_release_gets_the_new_people_and_portraits(self):
        demo.seed(self.db)
        self.db.execute("""UPDATE bookmarks SET author_name='Ada Example', author_username='ada.example',
                           author_avatar_url='data:image/svg+xml,old' WHERE tweet_id='9000000000000000001'""")
        self.db.commit()
        demo.seed(self.db)
        row = self.db.execute("SELECT * FROM bookmarks WHERE tweet_id='9000000000000000001'").fetchone()
        self.assertEqual((row["author_name"], row["author_username"]), ("Maya Okafor", "maya.okafor"))
        self.assertTrue(row["author_avatar_url"].startswith("data:image/jpeg;base64,"))
        self.assertEqual(row["source_url"], "https://example.com/demo/9000000000000000001")

    def test_demo_decorates_a_demo_database_from_an_older_release(self):
        demo.seed(self.db)
        self.db.execute("UPDATE bookmarks SET author_avatar_url=NULL, metrics_json=NULL")
        self.db.execute("DELETE FROM media")
        self.db.commit()
        self.assertEqual(demo.seed(self.db), 0)  # no new proposals ...
        self.assertEqual(self.db.execute("SELECT count(*) FROM bookmarks WHERE author_avatar_url IS NULL").fetchone()[0], 0)
        self.assertEqual(self.db.execute("SELECT count(*) FROM media").fetchone()[0], 3)  # ... but the pictures are back

    def test_card_text(self):
        demo.seed(self.db)
        state = cq.next_card(self.db)
        text = card_text(state["card"])
        self.assertIn(state["card"]["action_id"], text)
        self.assertIn(state["card"]["title"], text)
        self.assertIn("Why:", text)


class PackagingTests(unittest.TestCase):
    def setUp(self):
        self.meta = tomllib.loads((helpers.ROOT / "pyproject.toml").read_text())

    def test_metadata(self):
        project = self.meta["project"]
        self.assertEqual(project["name"], "unhoard")
        self.assertEqual(project["requires-python"], ">=3.11")
        self.assertEqual(project["dependencies"], [])  # stdlib-only core
        self.assertNotIn("optional-dependencies", project)
        self.assertEqual(project["scripts"]["unhoard"], "unhoard.cli:main")
        self.assertEqual(project["entry-points"]["omnigent.extensions"]["unhoard"],
                         "unhoard_ext.plugin:get_manifest")

    def test_one_version_everywhere(self):
        # pyproject reads unhoard.__version__; the Omnigent manifest may not import it, so it repeats the string
        import unhoard
        self.assertEqual(self.meta["project"]["dynamic"], ["version"])
        self.assertEqual(self.meta["tool"]["setuptools"]["dynamic"]["version"], {"attr": "unhoard.__version__"})
        self.assertIn(f'version="{unhoard.__version__}"', (EXT / "plugin.py").read_text())
        changelog = (helpers.ROOT / "CHANGELOG.md").read_text()
        self.assertIn(f"## [{unhoard.__version__}]", changelog)  # every release has its changelog section

    def test_package_data_files_exist(self):
        for package, patterns in self.meta["tool"]["setuptools"]["package-data"].items():
            for pattern in patterns:
                self.assertTrue(list((helpers.ROOT / "src" / package).glob(pattern)), f"{package}/{pattern}")


class ExtensionScaffoldTests(unittest.TestCase):
    def test_bundle_is_self_contained_and_has_no_network_apis(self):
        js = (EXT / "dist" / "extension.js").read_text()
        for banned in ("fetch(", "XMLHttpRequest", "WebSocket", "import(", "import ", "require(", "eval("):
            self.assertNotIn(banned, js)
        self.assertIn("unhoard serve", js)
        self.assertIn("sessions.create", js)  # says honestly what is missing

    def test_manifest_source_follows_documented_rules(self):
        tree = ast.parse((EXT / "plugin.py").read_text())
        consts = {n.targets[0].id: n.value for n in tree.body if isinstance(n, ast.Assign)}
        self.assertEqual(ast.literal_eval(consts["EXTENSION_ID"]), "vicennial.unhoard")
        text = (EXT / "plugin.py").read_text()
        self.assertIn('browser="dist/extension.js"', text)
        self.assertIn('route="bookmarks"', text)  # one safe segment
        self.assertIn("f\"{EXTENSION_ID}.primary-nav\"", text)  # contribution ids start with the extension id

    @unittest.skipUnless(HAS_OMNIGENT_EXT, "installed Omnigent has no omnigent.extensions")
    def test_manifest_passes_omnigent_check(self):
        from omnigent.extensions import check_extension_package
        from unhoard_ext.plugin import get_manifest
        check_extension_package(get_manifest(), project_root=helpers.ROOT, package_root=EXT)


if __name__ == "__main__":
    unittest.main()
