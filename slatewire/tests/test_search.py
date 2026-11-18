"""Tests for the full-text search module (no Flask, no network).

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

import search  # noqa: E402

NOW = 1_700_000_000.0


def _article(article_id, title, desc="", *, source="Example", category="radar",
             severity="high", published_ts=NOW, **extra):
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


def _run(query, live=None, archive=None, briefs=None, **kwargs):
    kwargs.setdefault("now", NOW)
    return search.search(query, live=live, archive=archive, briefs=briefs, **kwargs)


class MatchingTest(unittest.TestCase):
    def test_empty_query_returns_nothing(self):
        result = _run("   ", live=[_article("a", "Anything at all")])
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["results"], [])
        self.assertEqual(result["terms"], [])

    def test_title_match_is_case_insensitive(self):
        result = _run("SONICWALL", live=[_article("a", "SonicWall fixes a firewall flaw")])
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["results"][0]["id"], "a")

    def test_description_and_brief_are_searchable(self):
        live = [_article("a", "A quiet headline", "The report names AcmeWidget in passing.")]
        self.assertEqual(_run("acmewidget", live=live)["total"], 1)
        self.assertEqual(_run("quarterly", live=live, briefs={"a:full": "Quarterly patch notes"})["total"], 1)

    def test_note_is_searchable(self):
        live = [_article("a", "A headline")]
        archive = [{"id": "a", "title": "A headline", "note": "Follow up with the blue team"}]
        self.assertEqual(_run("blue team", live=live, archive=archive)["total"], 1)

    def test_source_is_searchable(self):
        live = [_article("a", "A headline", source="SANS ISC")]
        self.assertEqual(_run("sans", live=live)["total"], 1)

    def test_every_word_must_match(self):
        live = [_article("a", "SonicWall fixes a firewall flaw")]
        self.assertEqual(_run("sonicwall firewall", live=live)["total"], 1)
        self.assertEqual(_run("sonicwall cisco", live=live)["total"], 0)

    def test_quoted_phrase_must_be_contiguous(self):
        live = [_article("a", "A headline", "This is a zero day exploit.")]
        self.assertEqual(_run('"zero day"', live=live)["total"], 1)
        self.assertEqual(_run('"day zero"', live=live)["total"], 0)

    def test_quoted_phrase_does_not_span_fields(self):
        live = [_article("a", "SonicWall", "MFA bypass details")]
        self.assertEqual(_run('"SonicWall MFA"', live=live)["total"], 0)

    def test_repeated_words_are_collapsed(self):
        self.assertEqual(search.parse_terms("Widget widget WIDGET"), ["widget"])

    def test_repeated_words_do_not_crowd_out_other_terms(self):
        live = [
            _article("both", "widget sonicwall story"),
            _article("widget-only", "widget story"),
        ]
        result = _run("widget " * 20 + "sonicwall", live=live)
        self.assertEqual([r["id"] for r in result["results"]], ["both"])

    def test_non_representative_rows_are_hidden_but_can_be_included(self):
        live = [_article("a", "Shared story"), _article("b", "Shared story", is_representative=False)]
        self.assertEqual(_run("shared", live=live)["total"], 1)
        self.assertEqual(_run("shared", live=live, include_duplicates=True)["total"], 2)


class MergeTest(unittest.TestCase):
    def test_same_id_live_and_archive_appears_once(self):
        live = [_article("a", "SonicWall story", desc="live copy")]
        archive = [{"id": "a", "title": "SonicWall story", "note": "saved note", "brief": "archived brief"}]
        result = _run("sonicwall", live=live, archive=archive)
        self.assertEqual(result["total"], 1)
        item = result["results"][0]
        self.assertTrue(item["in_live"])
        self.assertTrue(item["in_archive"])
        self.assertTrue(item["archived"])
        self.assertEqual(item["note"], "saved note")
        self.assertEqual(item["brief"], "archived brief")
        self.assertEqual(item["desc"], "live copy")   # live text still wins

    def test_archive_only_article_is_found(self):
        archive = [{"id": "z", "title": "Old Loki campaign", "source": "Old Source",
                    "desc": "A saved write-up", "published_ts": NOW}]
        result = _run("loki", archive=archive)
        self.assertEqual(result["total"], 1)
        item = result["results"][0]
        self.assertFalse(item["in_live"])
        self.assertTrue(item["in_archive"])

    def test_saved_archive_duplicate_stays_visible(self):
        live = [_article("a", "Shared story", is_representative=False)]
        archive = [{"id": "a", "title": "Shared story"}]
        self.assertEqual(_run("shared", live=live, archive=archive)["total"], 1)

    def test_search_does_not_mutate_inputs(self):
        live = [_article("a", "SonicWall story", desc="desc")]
        before = {key: live[0][key] for key in live[0]}
        _run("sonicwall", live=live, briefs={"a:full": "cached brief"})
        self.assertEqual(live[0], before)


class RankingTest(unittest.TestCase):
    def test_title_hit_outranks_brief_hit(self):
        live = [
            _article("brief", "A generic headline", desc="This brief mentions widget once."),
            _article("title", "Widget is the headline"),
        ]
        result = _run("widget", live=live)
        self.assertEqual([r["id"] for r in result["results"]], ["title", "brief"])

    def test_newer_story_wins_a_tie(self):
        live = [
            _article("old", "Widget story", published_ts=NOW - 5 * 86400),
            _article("new", "Widget story", published_ts=NOW - 3600),
        ]
        result = _run("widget", live=live)
        self.assertEqual(result["results"][0]["id"], "new")

    def test_quoted_phrase_gets_a_phrase_boost(self):
        live = [
            _article("title", "Zero day exploit hits routers"),
            _article("desc", "Weekly roundup", desc="A zero day exploit is discussed."),
        ]
        result = _run('"zero day"', live=live)
        self.assertEqual([r["id"] for r in result["results"]], ["title", "desc"])
        self.assertGreater(result["results"][0]["score"],
                           result["results"][1]["score"] + 10)

    def test_limit_is_applied(self):
        live = [_article(str(i), "Widget story " + str(i)) for i in range(5)]
        result = _run("widget", live=live, limit=2)
        self.assertEqual(result["total"], 5)
        self.assertEqual(result["count"], 2)
        self.assertEqual(len(result["results"]), 2)

    def test_limit_is_clamped(self):
        live = [_article(str(i), "Widget story") for i in range(3)]
        self.assertEqual(_run("widget", live=live, limit=0)["count"], 1)
        self.assertEqual(_run("widget", live=live, limit=9999)["limit"], search.MAX_LIMIT)


class FilterTest(unittest.TestCase):
    def setUp(self):
        self.live = [
            _article("crit", "Widget critical flaw", category="strike", severity="critical",
                     source="Alpha", published_ts=NOW - 3600,
                     kev=True, kev_cves=["CVE-2026-0001"]),
            _article("routine", "Widget routine note", category="radar", severity="routine",
                     source="Beta", published_ts=NOW - 10 * 86400),
            _article("cve", "Widget CVE-2026-0002", category="radar", severity="high",
                     source="Alpha", published_ts=NOW - 2 * 86400),
        ]

    def ids(self, **kwargs):
        return {r["id"] for r in _run("widget", live=self.live, **kwargs)["results"]}

    def test_category(self):
        self.assertEqual(self.ids(category="strike"), {"crit"})
        self.assertEqual(self.ids(category="STRIKE"), {"crit"})

    def test_severity(self):
        self.assertEqual(self.ids(severity="routine"), {"routine"})

    def test_source(self):
        self.assertEqual(self.ids(source="alpha"), {"crit", "cve"})

    def test_hidden_sources_are_skipped(self):
        self.assertEqual(self.ids(hidden_sources=["Beta"]), {"crit", "cve"})
        self.assertEqual(self.ids(hidden_sources=["beta"]), {"crit", "cve"})

    def test_explicit_source_filter_overrides_hidden(self):
        self.assertEqual(self.ids(source="beta", hidden_sources=["Beta"]), {"routine"})

    def test_days(self):
        self.assertEqual(self.ids(days=1), {"crit"})
        self.assertEqual(self.ids(days=3), {"crit", "cve"})

    def test_has_cve(self):
        self.assertEqual(self.ids(has_cve=True), {"crit", "cve"})
        self.assertEqual(self.ids(has_cve=False), {"routine"})

    def test_kev(self):
        self.assertEqual(self.ids(kev=True), {"crit"})
        self.assertEqual(self.ids(kev=False), {"routine", "cve"})

    def test_scope(self):
        archive = [{"id": "arch", "title": "Widget archived", "published_ts": NOW}]
        self.assertEqual(self.ids(scope="live"), {"crit", "routine", "cve"})
        result = _run("widget", live=self.live, archive=archive, scope="archive", now=NOW)
        self.assertEqual([r["id"] for r in result["results"]], ["arch"])


class SnippetAndFacetTest(unittest.TestCase):
    def test_snippet_comes_from_the_matching_body_field(self):
        live = [_article("a", "A vague headline",
                         "This report explains the widget exploit in enough detail.")]
        result = _run("widget", live=live)
        item = result["results"][0]
        self.assertEqual(item["snippet_field"], "description")
        self.assertIn("widget", item["snippet"].lower())
        self.assertIn("matched_fields", item)
        self.assertEqual(item["matched_fields"], ["description"])

    def test_title_only_match_has_no_snippet(self):
        live = [_article("a", "Widget headline", "Nothing else here.")]
        self.assertEqual(_run("widget", live=live)["results"][0]["snippet"], "")

    def test_facets_count_matched_articles(self):
        live = [
            _article("a", "Widget one", category="strike", severity="critical", source="Alpha"),
            _article("b", "Widget two", category="radar", severity="high", source="Beta"),
        ]
        facets = _run("widget", live=live)["facets"]
        self.assertEqual(facets["categories"], {"strike": 1, "radar": 1})
        self.assertEqual(facets["severities"], {"critical": 1, "high": 1})
        self.assertEqual(facets["sources"], {"Alpha": 1, "Beta": 1})


if __name__ == "__main__":
    unittest.main()
