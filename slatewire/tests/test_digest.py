"""Tests for the daily digest module (no Flask, no network).

Run from the slatewire folder:

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import articles  # noqa: E402
import digest  # noqa: E402

NOW = 1_700_000_000.0
HOUR = 3600.0


def _article(article_id, title, *, source="Example", category="radar",
             severity="high", published_ts=NOW - HOUR, desc="", **extra):
    article = {
        "id": article_id,
        "title": title,
        "desc": desc,
        "source": source,
        "category": category,
        "severity": severity,
        "published_ts": published_ts,
        "pubDate": "",
        "link": "https://example.test/" + article_id,
    }
    article.update(extra)
    return article


def _build(live, **kwargs):
    kwargs.setdefault("now", NOW)
    return digest.build(live, **kwargs)


class SharedHelpersTest(unittest.TestCase):
    def test_article_ts_prefers_published_ts(self):
        article = _article("a", "One", published_ts=12345)
        self.assertEqual(articles.article_ts(article), 12345.0)

    def test_article_ts_falls_back_to_pubdate(self):
        article = _article("a", "One", published_ts=0)
        article["pubDate"] = "Tue, 14 Nov 2023 22:13:20 GMT"
        self.assertEqual(articles.article_ts(article), 1700000000.0)

    def test_article_ts_zero_when_unknown(self):
        article = _article("a", "One", published_ts=0)
        article["pubDate"] = "not a date"
        self.assertEqual(articles.article_ts(article), 0.0)

    def test_source_count_prefers_source_count(self):
        self.assertEqual(articles.source_count({"source_count": 4, "dupe_count": 1}), 4)

    def test_source_count_falls_back_to_dupe_count(self):
        self.assertEqual(articles.source_count({"dupe_count": 2}), 3)

    def test_source_count_is_never_zero(self):
        self.assertEqual(articles.source_count({}), 1)
        self.assertEqual(articles.source_count({"source_count": "bad"}), 1)


class WindowTest(unittest.TestCase):
    def test_only_articles_inside_the_window_are_counted(self):
        live = [
            _article("in", "Inside", published_ts=NOW - 2 * HOUR),
            _article("old", "Outside", published_ts=NOW - 30 * HOUR),
        ]
        result = _build(live, hours=24)
        self.assertEqual(result["total"], 1)
        self.assertEqual([a["id"] for a in result["top_stories"]], ["in"])

    def test_undated_articles_are_skipped(self):
        live = [_article("none", "No date", published_ts=0)]
        self.assertEqual(_build(live)["total"], 0)

    def test_hours_are_clamped_to_the_allowed_range(self):
        live = [_article("a", "One", published_ts=NOW - 500 * HOUR)]
        self.assertEqual(_build(live, hours=0)["window_hours"], 1)
        self.assertEqual(_build(live, hours=99999)["window_hours"],
                         digest.WINDOW_HOURS_MAX)
        # 500 hours in is outside even the widest window.
        self.assertEqual(_build(live, hours=99999)["total"], 0)

    def test_top_is_clamped(self):
        live = [_article(f"a{i}", f"Story {i}") for i in range(30)]
        self.assertEqual(len(_build(live, top=0)["top_stories"]), 1)
        self.assertEqual(len(_build(live, top=999)["top_stories"]),
                         digest.TOP_MAX)


class FilterTest(unittest.TestCase):
    def test_duplicates_are_hidden_unless_requested(self):
        live = [
            _article("rep", "Representative", is_representative=True),
            _article("dupe", "Duplicate", is_representative=False),
        ]
        self.assertEqual(_build(live)["total"], 1)
        self.assertEqual(_build(live, include_duplicates=True)["total"], 2)

    def test_hidden_sources_are_skipped(self):
        live = [
            _article("a", "Alpha", source="Alpha"),
            _article("b", "Beta", source="Beta"),
        ]
        result = _build(live, hidden_sources=["beta"])
        self.assertEqual([a["id"] for a in result["top_stories"]], ["a"])
        self.assertEqual(result["counts"]["sources"], 1)

    def test_missing_or_invalid_entries_are_ignored(self):
        live = [None, "not an article", {}, _article("a", "Real"),
                _article("a", "Same id again")]
        self.assertEqual(_build(live)["total"], 1)


class CountsTest(unittest.TestCase):
    def test_severity_counts(self):
        live = [
            _article("c", "Critical", severity="critical"),
            _article("h1", "High one", severity="high"),
            _article("h2", "High two", severity="high"),
            _article("r", "Routine", severity="routine"),
            _article("o", "Other", severity="weird"),
        ]
        counts = _build(live)["counts"]
        self.assertEqual(counts["critical"], 1)
        self.assertEqual(counts["high"], 2)
        self.assertEqual(counts["routine"], 1)
        self.assertEqual(counts["other"], 1)
        self.assertEqual(counts["sources"], 1)

    def test_kev_count_and_distinct_cves(self):
        live = [
            _article("a", "Patch CVE-2026-1111 now", kev=True,
                     kev_cves=["CVE-2026-1111"]),
            _article("b", "Two bugs CVE-2026-1111 and CVE-2026-2222",
                     kev=True, kev_cves=["CVE-2026-1111", "CVE-2026-2222"]),
            _article("c", "No CVE here"),
        ]
        counts = _build(live)["counts"]
        self.assertEqual(counts["kev"], 2)
        self.assertEqual(counts["cves"], 2)

    def test_category_and_source_counts(self):
        live = [
            _article("a", "One", category="strike", source="Alpha"),
            _article("b", "Two", category="strike", source="Beta"),
            _article("c", "Three", category="radar", source="Alpha"),
        ]
        result = _build(live)
        self.assertEqual(result["categories"][0], {"id": "strike", "count": 2})
        self.assertEqual(result["sources"][0], {"name": "Alpha", "count": 2})


class RankingTest(unittest.TestCase):
    def test_top_stories_are_severity_first(self):
        live = [
            _article("routine", "Routine", severity="routine"),
            _article("critical", "Critical", severity="critical"),
            _article("high", "High", severity="high"),
        ]
        self.assertEqual([a["id"] for a in _build(live)["top_stories"]],
                         ["critical", "high", "routine"])

    def test_kev_breaks_a_severity_tie(self):
        live = [
            _article("plain", "Plain", severity="high"),
            _article("kev", "Exploited", severity="high", kev=True),
        ]
        self.assertEqual([a["id"] for a in _build(live)["top_stories"]],
                         ["kev", "plain"])

    def test_more_sources_break_a_tie(self):
        live = [
            _article("single", "Single", severity="high", source_count=1),
            _article("wide", "Wide", severity="high", source_count=6),
        ]
        self.assertEqual([a["id"] for a in _build(live)["top_stories"]],
                         ["wide", "single"])

    def test_newest_breaks_the_remaining_tie(self):
        live = [
            _article("old", "Same", published_ts=NOW - 3 * HOUR),
            _article("new", "Same", published_ts=NOW - HOUR),
        ]
        self.assertEqual([a["id"] for a in _build(live)["top_stories"]],
                         ["new", "old"])

    def test_critical_and_kev_lists_are_filtered(self):
        live = [
            _article("crit", "Critical", severity="critical"),
            _article("high", "High", severity="high"),
            _article("kev", "Exploited high", severity="high", kev=True),
        ]
        result = _build(live)
        self.assertEqual([a["id"] for a in result["critical"]], ["crit"])
        self.assertEqual([a["id"] for a in result["kev_stories"]], ["kev"])


class ExploitedTest(unittest.TestCase):
    def test_distinct_cves_are_merged_and_richest_detail_wins(self):
        live = [
            _article("a", "First report", kev=True, kev_cves=["CVE-2026-19868"],
                     kev_details=[{"cve": "CVE-2026-19868", "vendor": "SonicWall"}]),
            _article("b", "Second report", kev=True, kev_cves=["CVE-2026-19868"],
                     kev_details=[{"cve": "CVE-2026-19868", "vendor": "SonicWall",
                                   "product": "SonicOS", "name": "Auth bypass",
                                   "dateAdded": "2026-11-06",
                                   "dueDate": "2026-11-20", "ransomware": False}]),
        ]
        rows = _build(live)["exploited"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["product"], "SonicOS")
        self.assertEqual(rows[0]["dateAdded"], "2026-11-06")
        self.assertEqual(rows[0]["source"], "Example")

    def test_kev_cve_without_details_still_appears(self):
        live = [_article("a", "Only an id", kev=True, kev_cves=["CVE-2026-3333"])]
        rows = _build(live)["exploited"]
        self.assertEqual([r["cve"] for r in rows], ["CVE-2026-3333"])
        self.assertEqual(rows[0]["vendor"], "")

    def test_newest_addition_comes_first(self):
        live = [
            _article("a", "Older", kev=True, kev_cves=["CVE-2026-1111"],
                     kev_details=[{"cve": "CVE-2026-1111", "dateAdded": "2026-01-01"}]),
            _article("b", "Newer", kev=True, kev_cves=["CVE-2026-2222"],
                     kev_details=[{"cve": "CVE-2026-2222", "dateAdded": "2026-11-01"}]),
        ]
        self.assertEqual([r["cve"] for r in _build(live)["exploited"]],
                         ["CVE-2026-2222", "CVE-2026-1111"])


class SummaryTest(unittest.TestCase):
    def test_summary_keeps_the_fields_the_page_needs(self):
        live = [_article("a", "Title", desc="Body", source_count=3, kev=True)]
        row = _build(live)["top_stories"][0]
        self.assertEqual(row["id"], "a")
        self.assertEqual(row["desc"], "Body")
        self.assertEqual(row["source_count"], 3)
        self.assertTrue(row["kev"])
        self.assertNotIn("_in_live", row)


class NarrativeTest(unittest.TestCase):
    def test_empty_window(self):
        self.assertEqual(_build([])["narrative"],
                         "No new stories arrived in the last 24 hours.")

    def test_narrative_includes_counts_and_top_story(self):
        live = [
            _article("c", "Critical thing", severity="critical"),
            _article("h", "High thing", severity="high"),
        ]
        text = _build(live)["narrative"]
        self.assertIn("2 stories from 1 source", text)
        self.assertIn("1 critical", text)
        self.assertIn("Critical thing", text)

    def test_narrative_mentions_exploited_cves(self):
        live = [_article("a", "Exploited", kev=True, kev_cves=["CVE-2026-19868"],
                         kev_details=[{"cve": "CVE-2026-19868",
                                       "dateAdded": "2026-11-06"}])]
        text = _build(live)["narrative"]
        self.assertIn("known-exploited CVE is in the feed", text)
        self.assertIn("CVE-2026-19868", text)


if __name__ == "__main__":
    unittest.main()
