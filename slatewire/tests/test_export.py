"""Tests for the export module (no Flask, no network, no disk).

Run from the slatewire folder:

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import csv
import io
import json
import os
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import exporter  # noqa: E402

NOW = 1_700_000_000.0
DAY = 86400.0


def _article(article_id, title, *, source="Example", category="strike",
             severity="critical", published_ts=NOW - 3600, desc="",
             link=None, brief="", note="", kev=False, **extra):
    article = {
        "id": article_id,
        "title": title,
        "desc": desc,
        "source": source,
        "category": category,
        "severity": severity,
        "published_ts": published_ts,
        "pubDate": "",
        "link": "https://example.test/" + article_id if link is None else link,
        "brief": brief,
        "note": note,
        "kev": kev,
    }
    article.update(extra)
    return article


def _digest():
    story = _article("s1", "Edge gateway zero-day exploited",
                     source="Example Wire", kev=True,
                     desc="A CVE-2026-12345 flaw is under attack.",
                     kev_cves=["CVE-2026-12345"], source_count=3)
    return {
        "generated_at": "2023-11-14T22:13:20+00:00",
        "window_hours": 24,
        "since": "2023-11-13T22:13:20+00:00",
        "total": 7,
        "counts": {"critical": 1, "high": 3, "routine": 3, "kev": 1,
                   "cves": 1, "sources": 4},
        "categories": [{"id": "strike", "count": 4}],
        "sources": [{"name": "Example Wire", "count": 4}],
        "top_stories": [story],
        "critical": [story],
        "kev_stories": [story],
        "exploited": [{
            "cve": "CVE-2026-12345", "vendor": "Acme", "product": "Gateway",
            "name": "Edge Gateway", "dateAdded": "2026-01-02",
            "dueDate": "2026-01-23", "ransomware": True,
            "article_id": "s1", "title": story["title"],
            "source": story["source"], "published_ts": story["published_ts"],
        }],
        "narrative": "In the last 24 hours SLATEWIRE collected 7 stories.",
    }


class NormalizeFormatTest(unittest.TestCase):
    def test_aliases(self):
        for value, expected in (("md", "markdown"), ("markdown", "markdown"),
                                ("MARKDOWN", "markdown"), (".md", "markdown"),
                                ("csv", "csv"), (".CSV", "csv"),
                                ("json", "json"), (".json", "json")):
            self.assertEqual(exporter.normalize_format(value), expected)

    def test_unsupported(self):
        for value in ("html", "pdf", "", None, 12, "mdx"):
            self.assertIsNone(exporter.normalize_format(value))

    def test_mimetypes(self):
        self.assertIn("markdown", exporter.mimetype("md"))
        self.assertIn("csv", exporter.mimetype("csv"))
        self.assertIn("json", exporter.mimetype("json"))
        self.assertIn("markdown", exporter.mimetype("nonsense"))   # safe default


class ArticleRowTest(unittest.TestCase):
    def test_full_row(self):
        article = _article(
            "a1", "  A *loud* title  ", source=" Wire ", category="STRIKE",
            severity="CRITICAL", desc="About CVE-2026-1111 and cve-2026-2222.",
            brief="Brief text", note="My note", kev=True,
            source_count=3, in_archive=True, kev_cves=["CVE-2026-3333"])
        row = exporter.article_row(article)
        self.assertEqual(row["id"], "a1")
        self.assertEqual(row["title"], "A *loud* title")
        self.assertEqual(row["source"], "Wire")
        self.assertEqual(row["category"], "strike")
        self.assertEqual(row["severity"], "critical")
        self.assertEqual(row["published_utc"], "2023-11-14T21:13:20Z")
        self.assertEqual(row["link"], "https://example.test/a1")
        self.assertEqual(row["summary"], "About CVE-2026-1111 and cve-2026-2222.")
        self.assertEqual(row["brief"], "Brief text")
        self.assertEqual(row["note"], "My note")
        self.assertEqual(row["cves"],
                         ["CVE-2026-1111", "CVE-2026-2222", "CVE-2026-3333"])
        self.assertTrue(row["kev"])
        self.assertTrue(row["archived"])
        self.assertEqual(row["source_count"], 3)

    def test_unsafe_links_are_dropped(self):
        for link in ("javascript:alert(1)", "data:text/html,x", "ftp://x",
                     "example.test/a", ""):
            row = exporter.article_row(_article("a1", "t", link=link))
            self.assertEqual(row["link"], "")

    def test_missing_fields_are_blank(self):
        row = exporter.article_row({})
        self.assertEqual(row["id"], "")
        self.assertEqual(row["title"], "")
        self.assertEqual(row["published_utc"], "")
        self.assertEqual(row["cves"], [])
        self.assertEqual(row["source_count"], 1)

    def test_pubdate_fallback(self):
        row = exporter.article_row({"title": "t", "pubDate": "Tue, 14 Nov 2023 22:13:20 GMT"})
        self.assertEqual(row["published_utc"], "2023-11-14T22:13:20Z")
        self.assertEqual(row["published_raw"], "Tue, 14 Nov 2023 22:13:20 GMT")


class SelectArticlesTest(unittest.TestCase):
    def test_hidden_sources_are_skipped_unless_named(self):
        articles = [_article("a1", "One", source="Muted"),
                    _article("a2", "Two", source="Loud")]
        rows = exporter.select_articles(articles, hidden_sources=["Muted"], now=NOW)
        self.assertEqual([a["id"] for a in rows], ["a2"])
        rows = exporter.select_articles(articles, hidden_sources=["Muted"],
                                        source="Muted", now=NOW)
        self.assertEqual([a["id"] for a in rows], ["a1"])

    def test_category_severity_and_source_filters(self):
        articles = [
            _article("a1", "One", category="strike", severity="critical", source="A"),
            _article("a2", "Two", category="radar", severity="high", source="B"),
            _article("a3", "Three", category="strike", severity="high", source="A"),
        ]
        self.assertEqual([a["id"] for a in exporter.select_articles(
            articles, category="strike", now=NOW)], ["a1", "a3"])
        self.assertEqual([a["id"] for a in exporter.select_articles(
            articles, severity="HIGH", now=NOW)], ["a2", "a3"])
        self.assertEqual([a["id"] for a in exporter.select_articles(
            articles, source="a", now=NOW)], ["a1", "a3"])

    def test_days_cutoff_drops_old_and_undated_rows(self):
        articles = [_article("new", "New", published_ts=NOW - DAY),
                    _article("old", "Old", published_ts=NOW - 10 * DAY),
                    _article("none", "None", published_ts=0)]
        rows = exporter.select_articles(articles, days=7, now=NOW)
        self.assertEqual([a["id"] for a in rows], ["new"])

    def test_has_cve_and_kev(self):
        articles = [_article("a1", "No cve"),
                    _article("a2", "Has CVE-2026-1234", kev=True,
                             kev_cves=["CVE-2026-1234"])]
        self.assertEqual([a["id"] for a in exporter.select_articles(
            articles, has_cve=True, now=NOW)], ["a2"])
        self.assertEqual([a["id"] for a in exporter.select_articles(
            articles, has_cve=False, now=NOW)], ["a1"])
        self.assertEqual([a["id"] for a in exporter.select_articles(
            articles, kev=True, now=NOW)], ["a2"])

    def test_duplicates_are_skipped_unless_requested(self):
        articles = [_article("rep", "Story", is_representative=True),
                    _article("dup", "Story (other)", is_representative=False),
                    _article("arch", "Story (saved)", is_representative=False,
                             in_archive=True)]
        rows = exporter.select_articles(articles, now=NOW)
        self.assertEqual([a["id"] for a in rows], ["rep", "arch"])
        rows = exporter.select_articles(articles, include_duplicates=True, now=NOW)
        self.assertEqual([a["id"] for a in rows], ["rep", "dup", "arch"])

    def test_junk_rows_are_ignored(self):
        rows = exporter.select_articles([None, "x", {}, {"id": "ok"}], now=NOW)
        self.assertEqual([a["id"] for a in rows], ["ok"])

    def test_cap(self):
        articles = [_article(str(i), "t" + str(i)) for i in range(exporter.MAX_ITEMS + 5)]
        rows = exporter.select_articles(articles, now=NOW)
        self.assertEqual(len(rows), exporter.MAX_ITEMS)


class FilenameTest(unittest.TestCase):
    def test_descriptive_name(self):
        name = exporter.build_filename("search", "md", query="zero day",
                                       category="strike", now=NOW)
        self.assertEqual(name,
                         "slatewire-search-strike-zero-day-20231114-221320.md")

    def test_each_format_extension(self):
        self.assertTrue(exporter.build_filename("feed", "md", now=NOW).endswith(".md"))
        self.assertTrue(exporter.build_filename("feed", "csv", now=NOW).endswith(".csv"))
        self.assertTrue(exporter.build_filename("feed", "json", now=NOW).endswith(".json"))

    def test_bad_input_becomes_a_safe_slug(self):
        name = exporter.build_filename("../../etc", "md",
                                       query="a/b\\c: d?*", category="X Y", now=NOW)
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)
        self.assertNotIn(":", name)
        self.assertTrue(name.startswith("slatewire-etc-x-y-a-b-c-d-"))
        self.assertTrue(name.endswith(".md"))

    def test_long_query_is_truncated(self):
        name = exporter.build_filename("search", "md", query="a" * 200, now=NOW)
        self.assertLess(len(name), 140)
        self.assertIn("a" * 40, name)
        self.assertNotIn("a" * 41, name)


class MarkdownTest(unittest.TestCase):
    def test_article_markdown_has_context_and_escapes(self):
        articles = [
            _article("a1", "A *loud* [title]", source="Wire", category="strike",
                     severity="critical", desc="Plain summary.",
                     brief="A brief.", note="A note.", kev=True,
                     kev_cves=["CVE-2026-1234"], source_count=3),
            _article("a2", "Unsafe link", link="javascript:alert(1)"),
        ]
        body, filename, mime = exporter.render_articles(
            articles, "md", dataset="feed", title="SLATEWIRE feed export",
            version="v1.12.0", filters={"severity": "critical"}, now=NOW)
        self.assertEqual(mime, exporter.MIMETYPES["markdown"])
        self.assertTrue(filename.endswith(".md"))
        self.assertIn("# SLATEWIRE feed export", body)
        self.assertIn("- **Exported:** 2023-11-14T22:13:20Z", body)
        self.assertIn("- **App:** SLATEWIRE v1.12.0", body)
        self.assertIn("- **Items:** 2", body)
        self.assertIn("- **Filters:** severity=critical", body)
        self.assertIn("### A \\*loud\\* \\[title\\]", body)
        self.assertIn("**CVE:** CVE-2026-1234", body)
        self.assertIn("**Known exploited**", body)
        self.assertIn("**3 sources**", body)
        self.assertIn("[Open article](<https://example.test/a1>)", body)
        self.assertIn("### Unsafe link", body)
        self.assertNotIn("javascript:", body)
        self.assertNotIn("[Open article](<>)", body)

    def test_empty_export_still_has_a_header(self):
        body, _, _ = exporter.render_articles([], "md", now=NOW)
        self.assertIn("# SLATEWIRE feed export", body)
        self.assertIn("- **Items:** 0", body)


class CsvTest(unittest.TestCase):
    def _rows(self, body):
        return list(csv.reader(io.StringIO(body.lstrip(exporter.CSV_BOM))))

    def test_header_and_row(self):
        article = _article("a1", "Title, with comma", source="Wire",
                           desc="Summary", brief="Brief", note="Note",
                           kev=True, kev_cves=["CVE-2026-1234"], source_count=2)
        body, _, mime = exporter.render_articles(
            [article], "csv", dataset="feed", version="v1.12.0", now=NOW)
        self.assertEqual(mime, exporter.MIMETYPES["csv"])
        self.assertTrue(body.startswith(exporter.CSV_BOM))
        rows = self._rows(body)
        self.assertEqual(rows[0], list(exporter.CSV_COLUMNS))
        self.assertEqual(len(rows), 2)
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row["dataset"], "feed")
        self.assertEqual(row["app_version"], "v1.12.0")
        self.assertEqual(row["title"], "Title, with comma")
        self.assertEqual(row["cves"], "CVE-2026-1234")
        self.assertEqual(row["kev"], "yes")
        self.assertEqual(row["archived"], "no")
        self.assertEqual(row["source_count"], "2")

    def test_formula_injection_is_neutralised(self):
        articles = [
            _article("a1", "=HYPERLINK(\"http://x\")"),
            _article("a2", "+cmd"),
            _article("a3", "-10"),
            _article("a4", "@sum"),
        ]
        body, _, _ = exporter.render_articles(articles, "csv", now=NOW)
        rows = self._rows(body)
        titles = [row[4] for row in rows[1:]]
        self.assertEqual(titles,
                         ["'=HYPERLINK(\"http://x\")", "'+cmd", "'-10", "'@sum"])


class JsonTest(unittest.TestCase):
    def test_metadata_and_articles(self):
        articles = [_article("a1", "Ünicode title", kev=True)]
        body, filename, mime = exporter.render_articles(
            articles, "json", dataset="category", version="v1.12.0",
            filters={"category": "strike"}, now=NOW)
        self.assertEqual(mime, exporter.MIMETYPES["json"])
        self.assertTrue(filename.endswith(".json"))
        data = json.loads(body)
        self.assertEqual(data["metadata"]["app"], "SLATEWIRE")
        self.assertEqual(data["metadata"]["version"], "v1.12.0")
        self.assertEqual(data["metadata"]["dataset"], "category")
        self.assertEqual(data["metadata"]["count"], 1)
        self.assertEqual(data["metadata"]["filters"], {"category": "strike"})
        self.assertEqual(data["articles"][0]["title"], "Ünicode title")
        self.assertTrue(data["articles"][0]["kev"])
        self.assertEqual(data["articles"][0]["archived"], False)


class DigestTest(unittest.TestCase):
    def test_markdown(self):
        body, filename, mime = exporter.render_digest(
            _digest(), "md", version="v1.12.0", now=NOW)
        self.assertEqual(mime, exporter.MIMETYPES["markdown"])
        self.assertTrue(filename.startswith("slatewire-digest-"))
        self.assertIn("# SLATEWIRE digest", body)
        self.assertIn("- **Window:** last 24 hours", body)
        self.assertIn("- **Severity:** critical 1 · high 3 · routine 3", body)
        self.assertIn("## Summary", body)
        self.assertIn("## Top stories", body)
        self.assertIn("[Edge gateway zero-day exploited](<https://example.test/s1>)", body)
        self.assertIn("(critical, known exploited, 3 sources)", body)
        self.assertIn("## Critical", body)
        self.assertIn("## Known exploited CVEs", body)
        self.assertIn("| CVE-2026-12345 | Acme | Gateway | 2026-01-02 | 2026-01-23 | yes |", body)

    def test_csv_exports_top_stories(self):
        body, _, mime = exporter.render_digest(_digest(), "csv", version="v1.12.0", now=NOW)
        self.assertEqual(mime, exporter.MIMETYPES["csv"])
        self.assertTrue(body.startswith(exporter.CSV_BOM))
        rows = list(csv.reader(io.StringIO(body.lstrip(exporter.CSV_BOM))))
        self.assertEqual(rows[0], list(exporter.DIGEST_CSV_COLUMNS))
        row = dict(zip(rows[0], rows[1]))
        self.assertEqual(row["rank"], "1")
        self.assertEqual(row["id"], "s1")
        self.assertEqual(row["kev"], "yes")

    def test_json_keeps_the_digest_payload(self):
        body, _, _ = exporter.render_digest(_digest(), "json", version="v1.12.0", now=NOW)
        data = json.loads(body)
        self.assertEqual(data["metadata"]["dataset"], "digest")
        self.assertEqual(data["metadata"]["count"], 7)
        self.assertEqual(data["metadata"]["window_hours"], 24)
        self.assertEqual(data["digest"]["top_stories"][0]["id"], "s1")
        self.assertEqual(data["digest"]["exploited"][0]["cve"], "CVE-2026-12345")

    def test_empty_digest_does_not_crash(self):
        for fmt in ("md", "csv", "json"):
            body, _, _ = exporter.render_digest({}, fmt, now=NOW)
            self.assertIsInstance(body, str)
            self.assertTrue(body)


if __name__ == "__main__":
    unittest.main()
