"""Tests for the per-source health history (no Flask, no network, no disk).

Run from the slatewire folder:

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import fetcher  # noqa: E402
import source_health  # noqa: E402

NOW = 1_700_000_000.0
DAY = 86400.0


def _row(name="Alpha", state="ok", *, items=3, newest=NOW - 3600, reason="", ms=120):
    return {"name": name, "state": state, "items": items,
            "newest_ts": newest, "reason": reason, "ms": ms}


class ApplyTest(unittest.TestCase):
    def test_first_success_records_a_full_attempt(self):
        records = source_health.apply({}, [_row()], now=NOW)
        record = records["Alpha"]
        self.assertEqual(record["attempts"], 1)
        self.assertEqual(record["ok_count"], 1)
        self.assertEqual(record["streak_ok"], 1)
        self.assertEqual(record["streak_fail"], 0)
        self.assertEqual(record["last_ok_ts"], NOW)
        self.assertEqual(record["last_state"], "ok")
        self.assertFalse(record["just_alerted"])

        view = source_health.decorate(record, now=NOW)
        self.assertEqual(view["state"], "ok")
        self.assertEqual(view["success_rate"], 100)
        self.assertEqual(view["avg_ms"], 120)
        self.assertFalse(view["alert"])

    def test_failure_streak_alerts_once_at_the_threshold(self):
        records = source_health.apply({}, [_row(state="failed", reason="HTTP 404", ms=80)], now=NOW)
        self.assertEqual(source_health.decorate(records["Alpha"], NOW)["state"], "failed")

        records = source_health.apply(records, [_row(state="failed", reason="HTTP 404")], now=NOW + 60)
        self.assertEqual(source_health.decorate(records["Alpha"], NOW + 60)["state"], "failing")
        self.assertEqual(records["Alpha"]["streak_fail"], 2)
        self.assertFalse(records["Alpha"]["just_alerted"])

        records = source_health.apply(records, [_row(state="failed")], now=NOW + 120)
        view = source_health.decorate(records["Alpha"], NOW + 120)
        self.assertEqual(view["fail_streak"], source_health.ALERT_STREAK)
        self.assertTrue(view["alert"])
        self.assertTrue(view["just_alerted"])

        records = source_health.apply(records, [_row(state="failed")], now=NOW + 180)
        self.assertTrue(source_health.decorate(records["Alpha"], NOW + 180)["alert"])
        self.assertFalse(records["Alpha"]["just_alerted"])   # only the crossing refresh alerts

    def test_recovery_clears_the_streak_but_keeps_the_last_error_time(self):
        records = source_health.apply({}, [_row(state="failed", reason="cannot connect")], now=NOW)
        last_fail = records["Alpha"]["last_fail_ts"]
        records = source_health.apply(records, [_row()], now=NOW + 60)
        record = records["Alpha"]
        self.assertEqual(record["streak_fail"], 0)
        self.assertEqual(record["last_fail_ts"], last_fail)
        self.assertEqual(record["last_ok_ts"], NOW + 60)
        self.assertEqual(source_health.decorate(record, NOW + 60)["state"], "ok")
        self.assertFalse(record["just_alerted"])

    def test_empty_feed_is_reachable_but_degraded(self):
        records = source_health.apply({}, [_row(state="empty", items=0, newest=0,
                                                reason="reachable, but no usable articles")], now=NOW)
        record = records["Alpha"]
        self.assertEqual(record["ok_count"], 1)
        self.assertEqual(record["streak_fail"], 0)
        view = source_health.decorate(record, NOW)
        self.assertEqual(view["state"], "empty")
        self.assertEqual(view["success_rate"], 100)

    def test_stale_feed_is_flagged_and_boundary_is_exclusive(self):
        fresh = source_health.apply({}, [_row(newest=NOW - DAY)], now=NOW)
        self.assertEqual(source_health.decorate(fresh["Alpha"], NOW)["state"], "ok")

        edge = source_health.apply({}, [_row(newest=NOW - source_health.STALE_DAYS * DAY)], now=NOW)
        self.assertEqual(source_health.decorate(edge["Alpha"], NOW)["state"], "ok")

        old = source_health.apply(
            {}, [_row(newest=NOW - source_health.STALE_DAYS * DAY - 1)], now=NOW)
        self.assertEqual(source_health.decorate(old["Alpha"], NOW)["state"], "stale")

    def test_ok_feed_without_dates_is_not_called_stale(self):
        records = source_health.apply({}, [_row(newest=0)], now=NOW)
        self.assertEqual(source_health.decorate(records["Alpha"], NOW)["state"], "ok")

    def test_rolling_window_and_success_rate(self):
        records = {}
        for i in range(25):
            records = source_health.apply(records, [_row()], now=NOW + i)
        self.assertEqual(len(records["Alpha"]["recent"]), source_health.RECENT_MAX)
        self.assertEqual(source_health.decorate(records["Alpha"], NOW + 24)["success_rate"], 100)

        records = source_health.apply(records, [_row(state="failed")], now=NOW + 30)
        self.assertEqual(records["Alpha"]["recent"][-1], 0)
        self.assertEqual(source_health.decorate(records["Alpha"], NOW + 30)["success_rate"], 95)

    def test_average_latency_ignores_failures_and_keeps_the_newest(self):
        records = source_health.apply({}, [_row(ms=100)], now=NOW)
        records = source_health.apply(records, [_row(ms=300)], now=NOW + 1)
        records = source_health.apply(records, [_row(state="failed", ms=50)], now=NOW + 2)
        records = source_health.apply(records, [_row(ms=200)], now=NOW + 3)
        view = source_health.decorate(records["Alpha"], NOW + 3)
        self.assertEqual(view["avg_ms"], 200)     # (100 + 300 + 200) / 3
        self.assertEqual(view["last_ms"], 200)

    def test_latency_window_is_bounded(self):
        records = {}
        for i in range(12):
            records = source_health.apply(records, [_row(ms=(i + 1) * 100)], now=NOW + i)
        record = records["Alpha"]
        self.assertEqual(len(record["ms_recent"]), source_health.MS_MAX)
        self.assertEqual(record["ms_recent"][0], 300)
        self.assertEqual(source_health.decorate(record, NOW + 11)["avg_ms"], 750)

    def test_unknown_states_and_junk_rows_are_ignored(self):
        records = source_health.apply({}, [
            {"name": "Alpha", "state": "weird"},
            {"state": "ok"},
            "not a row",
            {"name": "", "state": "ok"},
            _row("Beta"),
        ], now=NOW)
        self.assertEqual(set(records), {"Beta"})


class NormalizeTest(unittest.TestCase):
    def test_garbage_becomes_empty(self):
        self.assertEqual(source_health.normalize(None), {})
        self.assertEqual(source_health.normalize([]), {})
        self.assertEqual(source_health.normalize({"Alpha": "junk"}), {})

    def test_accepts_flat_and_wrapped_shapes(self):
        flat = {"Alpha": {"attempts": 2, "ok_count": 1, "last_state": "ok"}}
        self.assertEqual(source_health.normalize(flat)["Alpha"]["attempts"], 2)
        wrapped = {"sources": flat}
        self.assertEqual(source_health.normalize(wrapped)["Alpha"]["ok_count"], 1)

    def test_bad_numbers_and_lists_are_cleaned(self):
        record = source_health.normalize({"Alpha": {
            "attempts": "9", "recent": [1, 0, None, "x", 1],
            "ms_recent": [-5, 0, 120.7, "x"], "newest_ts": 1_700_000_000,
            "last_state": "nonsense", "last_reason": "x" * 500,
        }})["Alpha"]
        self.assertEqual(record["attempts"], 3)      # max(cleaned recent length, numeric attempts)
        self.assertEqual(record["recent"], [1, 0, 1])
        self.assertEqual(record["ms_recent"], [120])
        self.assertEqual(record["newest_ts"], 1_700_000_000)
        self.assertEqual(record["last_state"], "unknown")
        self.assertEqual(len(record["last_reason"]), source_health.MAX_REASON)

    def test_names_are_truncated(self):
        name = "A" * 200
        records = source_health.normalize({name: {"attempts": 1}})
        self.assertEqual(len(next(iter(records))), source_health.MAX_NAME)


class ViewTest(unittest.TestCase):
    def test_status_order_then_sorted_extras(self):
        records = source_health.apply({}, [_row("Alpha"), _row("Beta")], now=NOW)
        rows = [_row("Beta"), _row("Gamma")]
        view = source_health.view(records, rows, now=NOW + 1)
        self.assertEqual([r["name"] for r in view], ["Beta", "Gamma", "Alpha"])
        self.assertEqual(view[1]["attempts"], 1)      # Gamma exists only in this refresh

    def test_current_row_overrides_display_fields(self):
        records = source_health.apply({}, [_row("Alpha", items=3, newest=NOW - DAY)], now=NOW)
        current = _row("Alpha", state="failed", items=0, newest=0, reason="HTTP 503", ms=900)
        view = source_health.view(records, [current], now=NOW + 10)
        self.assertEqual(view[0]["state"], "failed")
        self.assertEqual(view[0]["last_reason"], "HTTP 503")
        self.assertEqual(view[0]["last_items"], 0)
        self.assertEqual(view[0]["attempts"], 1)      # history is not incremented by a page read

    def test_no_records_and_no_rows(self):
        self.assertEqual(source_health.view({}, []), [])


class RetirementTest(unittest.TestCase):
    def test_hard_failures_retire_after_the_threshold(self):
        records = {}
        for i in range(source_health.RETIRE_AFTER):
            row = _row("Alpha", state="failed", reason="HTTP 404")
            row["hard"] = True
            records = source_health.apply(records, [row], now=NOW + i)
            self.assertEqual(records["Alpha"]["just_retired"], i == source_health.RETIRE_AFTER - 1)

        record = records["Alpha"]
        self.assertTrue(record["retired"])
        self.assertEqual(record["retired_reason"], "HTTP 404")
        self.assertEqual(record["hard_streak"], source_health.RETIRE_AFTER)
        view = source_health.decorate(record, NOW + 10)
        self.assertEqual(view["state"], "retired")
        self.assertEqual(source_health.retired_names(records), {"Alpha"})

    def test_temporary_failures_never_retire(self):
        records = {}
        for i in range(10):
            records = source_health.apply(
                records, [_row("Alpha", state="failed", reason="timed out / too slow")], now=NOW + i)
        self.assertFalse(records["Alpha"]["retired"])
        self.assertEqual(records["Alpha"]["hard_streak"], 0)
        self.assertEqual(source_health.retired_names(records), set())

    def test_a_soft_failure_resets_the_hard_streak(self):
        hard = _row("Alpha", state="failed", reason="HTTP 404")
        hard["hard"] = True
        records = source_health.apply({}, [hard], now=NOW)
        records = source_health.apply(records, [_row("Alpha", state="failed", reason="HTTP 503")], now=NOW + 1)
        self.assertEqual(records["Alpha"]["hard_streak"], 0)
        records = source_health.apply(records, [hard], now=NOW + 2)
        records = source_health.apply(records, [hard], now=NOW + 3)
        self.assertFalse(records["Alpha"]["retired"])   # only two hard failures in a row
        records = source_health.apply(records, [hard], now=NOW + 4)
        self.assertTrue(records["Alpha"]["retired"])

    def test_success_resets_the_hard_streak(self):
        hard = _row("Alpha", state="failed", reason="not valid RSS/Atom")
        hard["hard"] = True
        records = source_health.apply({}, [hard], now=NOW)
        records = source_health.apply(records, [hard], now=NOW + 1)
        records = source_health.apply(records, [_row("Alpha")], now=NOW + 2)
        self.assertEqual(records["Alpha"]["hard_streak"], 0)
        self.assertFalse(records["Alpha"]["retired"])

    def test_retirement_flag_is_reported_only_once(self):
        hard = _row("Alpha", state="failed", reason="HTTP 404")
        hard["hard"] = True
        records = {}
        for i in range(source_health.RETIRE_AFTER):
            records = source_health.apply(records, [hard], now=NOW + i)
        self.assertTrue(records["Alpha"]["just_retired"])

        # Retired sources are skipped on later refreshes, so no status row for
        # Alpha arrives -- the one-shot flag must still clear.
        records = source_health.apply(records, [_row("Beta")], now=NOW + 10)
        self.assertFalse(records["Alpha"]["just_retired"])
        self.assertTrue(records["Alpha"]["retired"])

    def test_revive_clears_the_retirement_but_keeps_the_history(self):
        hard = _row("Alpha", state="failed", reason="HTTP 410")
        hard["hard"] = True
        records = {}
        for i in range(source_health.RETIRE_AFTER):
            records = source_health.apply(records, [hard], now=NOW + i)
        attempts = records["Alpha"]["attempts"]

        revived = source_health.revive(records, "Alpha")
        record = revived["Alpha"]
        self.assertFalse(record["retired"])
        self.assertEqual(record["hard_streak"], 0)
        self.assertEqual(record["streak_fail"], 0)
        self.assertEqual(record["attempts"], attempts)
        self.assertEqual(record["retired_reason"], "")
        self.assertEqual(source_health.retired_names(revived), set())
        self.assertEqual(source_health.decorate(record, NOW + 10)["state"], "unknown")

        # It needs the full streak again before it can be retired a second time.
        records = source_health.apply(revived, [hard], now=NOW + 10)
        self.assertEqual(records["Alpha"]["hard_streak"], 1)
        self.assertFalse(records["Alpha"]["retired"])

    def test_revive_unknown_name_is_a_no_op(self):
        records = source_health.apply({}, [_row("Alpha")], now=NOW)
        self.assertEqual(source_health.revive(records, "Missing"), records)

    def test_country_and_language_come_from_the_status_row(self):
        row = _row("Alpha")
        row.update(country="ESP", lang="es")
        records = source_health.apply({}, [row], now=NOW)
        self.assertEqual(records["Alpha"]["country"], "ESP")
        self.assertEqual(records["Alpha"]["lang"], "es")
        self.assertEqual(source_health.decorate(records["Alpha"], NOW)["country"], "ESP")


class RetiredSourceSkipTest(unittest.TestCase):
    def test_retired_sources_are_not_fetched(self):
        old_sources = fetcher.SOURCES
        old_path = fetcher.HEALTH_PATH
        old_fetch = fetcher.fetch_source
        called = []
        with tempfile.TemporaryDirectory() as tmp:
            fetcher.SOURCES = [
                {"name": "Alpha", "url": "https://a.example/feed", "country": "USA", "lat": 1, "lon": 2},
                {"name": "Beta", "url": "https://b.example/feed", "country": "UK", "lat": 3, "lon": 4},
            ]
            fetcher.HEALTH_PATH = os.path.join(tmp, "source_health.json")
            hard = _row("Beta", state="failed", reason="HTTP 404")
            hard["hard"] = True
            records = {}
            for i in range(source_health.RETIRE_AFTER):
                records = source_health.apply(records, [hard], now=NOW + i)
            with open(fetcher.HEALTH_PATH, "w", encoding="utf-8") as f:
                json.dump(records, f)

            def fake_fetch(source):
                called.append(source["name"])
                return [{"title": "x", "desc": "", "source": source["name"]}], None

            fetcher.fetch_source = fake_fetch
            try:
                _fresh, _errors, status = fetcher.collect_fresh()
            finally:
                fetcher.SOURCES = old_sources
                fetcher.HEALTH_PATH = old_path
                fetcher.fetch_source = old_fetch

        self.assertEqual(called, ["Alpha"])
        self.assertEqual([row["name"] for row in status], ["Alpha"])
        self.assertEqual(fetcher.progress()["total"], 1)


class SummarizeTest(unittest.TestCase):
    def test_counts_every_state_and_alerts(self):
        rows = [
            {"state": "ok"},
            {"state": "failing", "alert": True},
            {"state": "failed"},
            {"state": "empty"},
            {"state": "stale"},
            {"state": "bogus"},
        ]
        summary = source_health.summarize(rows)
        self.assertEqual(summary["sources"], 6)
        self.assertEqual(summary["ok"], 1)
        self.assertEqual(summary["failing"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertEqual(summary["empty"], 1)
        self.assertEqual(summary["stale"], 1)
        self.assertEqual(summary["unknown"], 1)
        self.assertEqual(summary["unhealthy"], 4)
        self.assertEqual(summary["alerts"], 1)

    def test_empty_summary(self):
        summary = source_health.summarize([])
        self.assertEqual(summary["sources"], 0)
        self.assertEqual(summary["alerts"], 0)
        self.assertEqual(summary["last_check_ts"], 0)


class FetcherPersistenceTest(unittest.TestCase):
    def test_history_is_saved_across_refreshes(self):
        old_path = fetcher.HEALTH_PATH
        with tempfile.TemporaryDirectory() as tmp:
            fetcher.HEALTH_PATH = os.path.join(tmp, "source_health.json")
            try:
                first = fetcher._save_source_health([_row("Alpha")])
                self.assertEqual(first["Alpha"]["attempts"], 1)

                second = fetcher._save_source_health([_row("Alpha", state="failed")])
                self.assertEqual(second["Alpha"]["attempts"], 2)
                self.assertEqual(second["Alpha"]["streak_fail"], 1)

                with open(fetcher.HEALTH_PATH, encoding="utf-8") as f:
                    saved = json.load(f)
                self.assertEqual(saved["Alpha"]["attempts"], 2)
                self.assertEqual(saved["Alpha"]["last_state"], "failed")

                # An empty status list (e.g. a stubbed fetch) writes nothing.
                self.assertEqual(fetcher._save_source_health([]), {})
            finally:
                fetcher.HEALTH_PATH = old_path


if __name__ == "__main__":
    unittest.main()
