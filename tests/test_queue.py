import helpers
import datetime as dt
import unittest

from unhoard import queue as cq
from unhoard.launch import TargetChanged

NOW = dt.datetime(2026, 10, 17, 12, 0, tzinfo=dt.timezone.utc)
HIGH, MED, LOW = "BMK-0000000B", "BMK-0000000C", "BMK-0000000A"


class QueueTests(helpers.TempCase):
    """Ported from the reference card-queue tests, plus the generic additions."""

    def setUp(self):
        super().setUp()
        helpers.seed_basic(self.db)

    def row(self, aid):
        return dict(self.db.execute("SELECT * FROM actions WHERE action_id=?", (aid,)).fetchone())

    def test_next_card_prefers_priority_then_confidence(self):
        out = cq.next_card(self.db, now=NOW)
        self.assertEqual(out["card"]["action_id"], HIGH)
        self.assertEqual(out["card"]["why_saved"], "why two")
        self.assertEqual(out["waiting"], 3)

    def test_confidence_then_newest_break_ties(self):
        self.db.execute("UPDATE actions SET priority='medium'")
        self.assertEqual(cq.next_card(self.db, now=NOW)["card"]["action_id"], HIGH)  # tweet 2 has 0.9
        self.db.execute("UPDATE enrichments SET confidence=0.5")
        self.db.commit()
        self.assertEqual(cq.next_card(self.db, now=NOW)["card"]["action_id"], MED)  # newest created_at

    def test_later_snoozes_and_next_card_moves_on(self):
        cq.decide(self.db, HIGH, "later", now=NOW)
        self.assertEqual(cq.next_card(self.db, now=NOW)["card"]["action_id"], MED)
        self.assertEqual(self.row(HIGH)["status"], "proposed")
        back = cq.next_card(self.db, now=NOW + dt.timedelta(days=cq.LATER_DAYS, minutes=1))
        self.assertEqual(back["card"]["action_id"], HIGH)

    def test_skip_dismisses(self):
        out = cq.decide(self.db, HIGH, "skip", now=NOW)
        self.assertEqual(self.row(HIGH)["status"], "skipped")
        self.assertEqual(out["status"], "skipped")

    def test_go_starts_worker_with_note(self):
        seen = {}

        def launch(card, note):
            seen.update(card=card, note=note)
            return {"ref": "sess-1", "status": "started"}

        out = cq.decide(self.db, HIGH, "go", note="focus on EU rules", launch=launch, now=NOW)
        row = self.row(HIGH)
        self.assertEqual((row["status"], row["launch_ref"], row["launch_status"]), ("accepted", "sess-1", "started"))
        self.assertEqual(row["note"], "focus on EU rules")
        self.assertEqual(seen["note"], "focus on EU rules")
        self.assertEqual(seen["card"]["title"], "High task")
        self.assertEqual(seen["card"]["source_url"], "https://x.com/b.example/status/2")
        self.assertEqual(out["launch_ref"], "sess-1")

    def test_go_failure_rolls_back_to_proposed(self):
        def fail(_card, _note):
            raise RuntimeError("omnigent down")

        with self.assertRaises(RuntimeError):
            cq.decide(self.db, HIGH, "go", note="x", launch=fail, now=NOW)
        row = self.row(HIGH)
        self.assertEqual((row["status"], row["launch_status"], row["note"]), ("proposed", "launch_failed", None))
        self.assertIn("omnigent down", row["launch_error"])
        self.assertIsNone(row["card_decided_at"])  # a failed launch is not an answered card
        self.assertEqual(cq.next_card(self.db, now=NOW)["card"]["action_id"], HIGH)

    def test_target_changed_rolls_back_without_a_failure_trace_and_keeps_the_note(self):
        def moved(_card, _note):
            raise TargetChanged("print", "omnigent")

        with self.assertRaises(TargetChanged):
            cq.decide(self.db, HIGH, "go", note="focus on the free tier", launch=moved, now=NOW)
        row = self.row(HIGH)
        self.assertEqual((row["status"], row["decided_at"]), ("proposed", None))
        self.assertIsNone(row["launch_status"])  # nothing failed, so the card must not say "Last launch failed"
        self.assertIsNone(row["launch_error"])
        self.assertEqual(row["note"], "focus on the free tier")
        card = cq.next_card(self.db, now=NOW)["card"]
        self.assertEqual(card["action_id"], HIGH)  # still open, and the next Go works
        out = cq.decide(self.db, HIGH, "go", note="focus on the free tier", launch=lambda c, n: {"ref": "r1"}, now=NOW)
        self.assertEqual(out["launch_ref"], "r1")

    def test_launch_result_without_ref_rolls_back(self):
        with self.assertRaises(KeyError):
            cq.decide(self.db, HIGH, "go", launch=lambda c, n: {}, now=NOW)
        self.assertEqual(self.row(HIGH)["status"], "proposed")

    def test_second_go_is_rejected_not_relaunched(self):
        calls = []
        cq.decide(self.db, HIGH, "go", launch=lambda c, n: calls.append(1) or {"ref": "r"}, now=NOW)
        with self.assertRaises(cq.NotOpenError):
            cq.decide(self.db, HIGH, "go", launch=lambda c, n: calls.append(1) or {"ref": "r"}, now=NOW)
        self.assertEqual(len(calls), 1)

    def test_daily_limit_and_force(self):
        day = NOW.strftime("%Y-%m-%d") + "T09:00:00Z"
        self.db.execute("UPDATE actions SET status='skipped',card_decided_at=? WHERE action_id IN (?,?)", (day, LOW, MED))
        self.db.execute("UPDATE actions SET snoozed_until='2026-10-20T00:00:00Z',card_decided_at=? WHERE action_id=?",
                        (NOW.strftime("%Y-%m-%dT%H:%M:%SZ"), HIGH))
        self.db.commit()
        self.assertEqual(cq.next_card(self.db, now=NOW)["reason"], "daily_limit")
        self.assertEqual(cq.next_card(self.db, force=True, now=NOW)["reason"], "empty")

    def test_launch_bookkeeping_does_not_count_as_a_card(self):
        self.db.execute("UPDATE actions SET status='accepted',decided_at=? WHERE action_id=?", (NOW.isoformat(), LOW))
        self.db.commit()
        self.assertEqual(cq.next_card(self.db, now=NOW)["decided_today"], 0)
        cq.decide(self.db, HIGH, "skip", now=NOW)
        self.assertEqual(cq.next_card(self.db, now=NOW)["decided_today"], 1)

    def test_rejects_bad_input(self):
        with self.assertRaises(ValueError):
            cq.decide(self.db, "nope", "go")
        with self.assertRaises(ValueError):
            cq.decide(self.db, HIGH, "maybe")
        with self.assertRaises(LookupError):
            cq.decide(self.db, "BMK-0000000F", "later")
        with self.assertRaises(ValueError):
            cq.decide(self.db, HIGH, "go")  # Go needs a launcher

    def test_note_is_cleaned_and_bounded(self):
        self.assertEqual(cq.clean_note("  hi\x00 there\r\nline2 "), "hi there\nline2")
        with self.assertRaises(ValueError):
            cq.clean_note("x" * (cq.MAX_NOTE + 1))
        with self.assertRaises(ValueError):
            cq.clean_note(5)

    def test_board_splits_picks_and_waiting(self):
        self.db.execute("INSERT INTO actions(action_id,tweet_id,title,brief,label,priority,created_at) "
                        "VALUES('BMK-0000000D','2','Fourth','d','work','low','2026-10-13T00:00:00Z')")
        self.db.commit()
        b = cq.board(self.db, now=NOW)
        self.assertEqual([c["action_id"] for c in b["picks"]], [HIGH, MED, "BMK-0000000D"])  # D: low, but tweet 2 has 0.9
        self.assertEqual([c["action_id"] for c in b["waiting"]], [LOW])
        self.assertEqual(b["labels"], ["personal", "work"])
        cq.decide(self.db, HIGH, "later", now=NOW)
        b = cq.board(self.db, now=NOW)
        self.assertEqual(len(b["picks"]), 2)  # one slot used today
        self.assertEqual(b["later_count"], 1)
        self.assertEqual(len(cq.board(self.db, now=NOW, more=True)["picks"]), 3)

    def test_label_filter(self):
        self.assertEqual([c["action_id"] for c in cq.ranked(self.db, NOW, label="work")], [HIGH])


if __name__ == "__main__":
    unittest.main()
