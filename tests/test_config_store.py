import helpers  # noqa: F401  (makes src importable)
import os
import sqlite3
import unittest
from pathlib import Path
from unittest import mock

from unhoard import config as C
from unhoard.store import connect, counts, get_meta, set_meta, timestamp
import datetime as dt


class PathTests(unittest.TestCase):
    def test_env_vars_win_and_are_absolute(self):
        paths = C.resolve_paths({"UNHOARD_HOME": "/tmp/bk-data", "UNHOARD_CONFIG": "/tmp/bk-cfg.toml"})
        self.assertEqual(paths.data_dir, Path("/tmp/bk-data").resolve())
        self.assertEqual(paths.config_file, Path("/tmp/bk-cfg.toml").resolve())
        self.assertEqual(paths.db_file.name, "bookmarks.sqlite3")

    def test_relative_env_path_is_resolved_once(self):
        paths = C.resolve_paths({"UNHOARD_HOME": "rel-data"})
        self.assertTrue(paths.data_dir.is_absolute())

    def test_xdg_defaults(self):
        paths = C.resolve_paths({"XDG_DATA_HOME": "/tmp/xdg-d", "XDG_CONFIG_HOME": "/tmp/xdg-c"})
        self.assertEqual(paths.data_dir, Path("/tmp/xdg-d/unhoard").resolve())
        self.assertEqual(paths.config_file, Path("/tmp/xdg-c/unhoard/config.toml").resolve())

    def test_home_based_default(self):
        with mock.patch.dict(os.environ, {"HOME": "/tmp/fakehome"}):
            paths = C.resolve_paths({})
        self.assertEqual(paths.data_dir, Path("/tmp/fakehome/.local/share/unhoard").resolve())
        self.assertEqual(paths.config_file, Path("/tmp/fakehome/.config/unhoard/config.toml").resolve())

    def test_config_env_may_point_at_a_directory(self):
        d = Path(os.environ.get("TMPDIR", "/tmp"))
        paths = C.resolve_paths({"UNHOARD_CONFIG": str(d)})
        self.assertEqual(paths.config_file, d.resolve() / "config.toml")


class ConfigTests(helpers.TempCase):
    def test_defaults_and_merge(self):
        cfg = self.config(queue={"daily_cards": 5})
        self.assertEqual(cfg.get("queue", "daily_cards"), 5)
        self.assertEqual(cfg.get("queue", "later_days"), 3)
        self.assertEqual(cfg.get("omnigent", "api"), "http://127.0.0.1:6767")
        self.assertEqual(cfg.problems, [])

    def test_token_prefers_environment(self):
        cfg = C.Config({"x": {"bearer_token": "from-file"}}, self.paths, env={"X_BEARER_TOKEN": "from-env"})
        self.assertEqual(cfg.x_token, "from-env")
        self.assertEqual(C.Config({"x": {"bearer_token": "from-file"}}, self.paths, env={}).x_token, "from-file")

    def test_load_reads_toml_and_reports_bad_types(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text('[enrich]\ncommand = "claude -p"\nmax_tasks = "three"\n[launch]\nadapter = "x"\n')
        cfg = C.Config.load(self.paths, env={})
        self.assertEqual(len(cfg.problems), 3)

    def test_load_rejects_invalid_toml(self):
        self.paths.config_file.parent.mkdir(parents=True)
        self.paths.config_file.write_text("[broken")
        with self.assertRaises(C.ConfigError):
            C.Config.load(self.paths, env={})

    def test_about_me_file_is_bounded(self):
        f = self.tmp / "about.md"
        f.write_text("I build things. " * 1000)
        cfg = self.config(enrich={"about_me_file": str(f)})
        self.assertLessEqual(len(cfg.about_me(100)), 100)
        self.assertEqual(self.config().about_me(), "")

    def test_template_is_valid_toml_with_defaults(self):
        import tomllib
        text = C.TEMPLATE.format(user_id='""', agent='"claude"', read_command='["claude", "-p"]',
                                 relate_command='["claude", "-p", "--tools", "Read"]', adapter='"print"')
        data = tomllib.loads(text)
        self.assertEqual(data["enrich"]["agent"], "claude")
        self.assertEqual(data["enrich"]["read_command"], ["claude", "-p"])
        self.assertEqual(data["enrich"]["relate_command"], ["claude", "-p", "--tools", "Read"])
        self.assertEqual(data["context"]["folders"], [])
        self.assertEqual(C.Config(data, self.paths, env={}).problems, [])


class StoreTests(helpers.TempCase):
    def test_schema_and_permissions(self):
        names = {r[0] for r in self.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self.assertTrue({"bookmarks", "media", "enrichments", "actions", "meta"} <= names)
        cols = {r[1] for r in self.db.execute("PRAGMA table_info(actions)")}
        self.assertTrue({"label", "priority", "status", "snoozed_until", "card_decided_at", "launch_ref",
                         "launch_status", "note"} <= cols)
        self.assertEqual(oct(self.paths.db_file.stat().st_mode & 0o777), "0o600")
        self.assertEqual(oct(self.paths.data_dir.stat().st_mode & 0o777), "0o700")

    def test_connect_is_idempotent_and_keeps_data(self):
        set_meta(self.db, "k", "v")
        self.db.commit()
        again = connect(self.paths)
        self.addCleanup(again.close)
        self.assertEqual(get_meta(again, "k"), "v")
        self.assertIsNone(get_meta(again, "missing"))

    def test_status_check_constraint(self):
        helpers.seed_basic(self.db)
        with self.assertRaises(sqlite3.IntegrityError):
            self.db.execute("UPDATE actions SET status='bogus'")

    def test_cascade_delete_and_counts(self):
        helpers.seed_basic(self.db)
        self.assertEqual(counts(self.db)["proposed"], 3)
        self.db.execute("DELETE FROM bookmarks WHERE tweet_id='1'")
        self.assertEqual(counts(self.db)["proposed"], 1)

    def test_timestamp_is_fixed_width_utc(self):
        stamp = timestamp(dt.datetime(2026, 1, 2, 3, 4, 5, 999, tzinfo=dt.timezone(dt.timedelta(hours=2))))
        self.assertEqual(stamp, "2026-01-02T01:04:05Z")



class SchemaMigrationTests(helpers.TempCase):
    def test_v1_database_gets_the_link_summary_column(self):
        import sqlite3
        from unhoard.store import connect
        self.db.close()
        self.paths.db_file.unlink()
        raw = sqlite3.connect(self.paths.db_file)
        raw.executescript("""CREATE TABLE bookmarks(tweet_id TEXT PRIMARY KEY, text TEXT NOT NULL, author_id TEXT,
            author_username TEXT, author_name TEXT, tweet_created_at TEXT, first_seen_at TEXT NOT NULL,
            last_seen_at TEXT NOT NULL, source_url TEXT NOT NULL, raw_json TEXT NOT NULL,
            state TEXT NOT NULL DEFAULT 'pending', error TEXT);
          CREATE TABLE enrichments(tweet_id TEXT PRIMARY KEY, summary TEXT NOT NULL, why_saved TEXT NOT NULL,
            confidence REAL NOT NULL, enriched_at TEXT NOT NULL);
          INSERT INTO bookmarks(tweet_id,text,first_seen_at,last_seen_at,source_url,raw_json) VALUES('1','t','a','a','u','{}');
          INSERT INTO enrichments VALUES('1','s','w',0.5,'now');""")
        raw.commit()
        raw.close()
        db = connect(self.paths)
        self.addCleanup(db.close)
        row = db.execute("SELECT summary, link_summary FROM enrichments").fetchone()
        self.assertEqual((row["summary"], row["link_summary"]), ("s", ""))


OLD_V2 = """CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE bookmarks(tweet_id TEXT PRIMARY KEY, text TEXT NOT NULL, author_id TEXT, author_username TEXT,
  author_name TEXT, tweet_created_at TEXT, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
  source_url TEXT NOT NULL, raw_json TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','enriched','error')), error TEXT);
CREATE TABLE media(media_key TEXT PRIMARY KEY, tweet_id TEXT NOT NULL REFERENCES bookmarks(tweet_id) ON DELETE CASCADE,
  media_type TEXT, url TEXT, alt_text TEXT);
CREATE TABLE enrichments(tweet_id TEXT PRIMARY KEY, summary TEXT NOT NULL, why_saved TEXT NOT NULL,
  confidence REAL NOT NULL, enriched_at TEXT NOT NULL, link_summary TEXT NOT NULL DEFAULT '');
INSERT INTO bookmarks(tweet_id,text,first_seen_at,last_seen_at,source_url,raw_json) VALUES('1','t','a','a','u','{}');
INSERT INTO media(media_key,tweet_id,media_type,url) VALUES('m','1','photo','https://x/y.jpg');
"""


class SafeMigrationTests(helpers.TempCase):
    def old_db(self, version=2):
        self.db.close()
        for suffix in ("", "-wal", "-shm"):
            Path(str(self.paths.db_file) + suffix).unlink(missing_ok=True)
        raw = sqlite3.connect(self.paths.db_file)
        raw.executescript(OLD_V2)
        raw.execute(f"PRAGMA user_version={version}")
        raw.commit()
        raw.close()

    def columns(self, db, table):
        return {r[1] for r in db.execute(f"PRAGMA table_info({table})")}

    def test_schema_2_database_gets_the_new_columns_and_keeps_its_rows(self):
        from unhoard import store
        self.old_db(2)
        db = connect(self.paths)
        self.addCleanup(db.close)
        self.assertTrue({"author_avatar_url", "author_verified", "metrics_json", "text_display", "links_json"}
                        <= self.columns(db, "bookmarks"))
        self.assertTrue({"position", "video_url", "width", "height", "duration_ms"} <= self.columns(db, "media"))
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)
        self.assertEqual(db.execute("SELECT url, position FROM media").fetchone()[:], ("https://x/y.jpg", 0))
        self.assertEqual(db.execute("SELECT count(*) FROM bookmarks").fetchone()[0], 1)

    def test_user_version_is_never_lowered(self):
        self.old_db(99)  # a database written by a newer release
        db = connect(self.paths)
        self.addCleanup(db.close)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], 99)

    def test_migrate_twice_and_with_existing_columns_is_harmless(self):
        from unhoard import store
        self.old_db(2)
        db = connect(self.paths)
        self.addCleanup(db.close)
        db.execute("PRAGMA user_version=2")  # pretend the version bump was lost: the columns already exist
        store.migrate(db)
        store.migrate(db)
        self.assertEqual(db.execute("PRAGMA user_version").fetchone()[0], store.SCHEMA_VERSION)

    def test_two_processes_starting_together_do_not_both_alter(self):
        import threading
        self.old_db(2)
        errors, dbs = [], []

        def start():
            try:
                connect(self.paths).close()
            except Exception as exc:  # noqa: BLE001 - the test reports whatever happened
                errors.append(exc)

        threads = [threading.Thread(target=start) for _ in range(6)]
        [t.start() for t in threads]
        [t.join(timeout=20) for t in threads]
        self.assertEqual(errors, [])  # no "duplicate column name"

    def test_a_failing_migration_rolls_back_completely(self):
        from unhoard import store
        self.old_db(2)
        raw = sqlite3.connect(self.paths.db_file)
        raw.row_factory = sqlite3.Row
        bad = store.ADDED_COLUMNS + (("bookmarks", "zzz", "NOT A TYPE ((("),)
        with mock.patch.object(store, "ADDED_COLUMNS", bad), self.assertRaises(sqlite3.Error):
            store.migrate(raw)
        self.assertNotIn("author_avatar_url", self.columns(raw, "bookmarks"))  # the earlier ALTERs were undone
        self.assertEqual(raw.execute("PRAGMA user_version").fetchone()[0], 2)
        raw.close()


if __name__ == "__main__":
    unittest.main()
