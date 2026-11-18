"""Tests for user-added RSS sources and OPML import (no network, no disk).

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

import custom_sources  # noqa: E402
import fetcher  # noqa: E402


def _src(name="Alpha", url="https://alpha.example/feed", **kw):
    row = {"name": name, "url": url}
    row.update(kw)
    return row


OPML = """<?xml version="1.0" encoding="UTF-8"?>
<opml version="2.0">
  <head><title>Subscriptions</title></head>
  <body>
    <outline text="Security">
      <outline type="rss" text="Alpha" xmlUrl="https://alpha.example/feed"/>
      <outline type="rss" title="Beta" xmlUrl="https://beta.example/rss"/>
    </outline>
    <outline type="rss" text="Broken" xmlUrl="ftp://broken.example/feed"/>
  </body>
</opml>
"""


class CleanTest(unittest.TestCase):
    def test_minimal_source_gets_safe_defaults(self):
        source, err = custom_sources.clean(_src())
        self.assertEqual(err, "")
        self.assertEqual(source["name"], "Alpha")
        self.assertEqual(source["country"], "")
        self.assertEqual(source["lang"], "en")
        self.assertEqual(source["lat"], 0.0)
        self.assertEqual(source["lon"], 0.0)
        self.assertTrue(source["custom"])

    def test_rejects_bad_input(self):
        cases = [
            ({"url": "https://x.example/feed"}, "name"),
            (_src(url="ftp://x.example/feed"), "http"),
            (_src(url="javascript:alert(1)"), "http"),
            (_src(country="TOOLONG"), "country"),
            (_src(lat="10"), "both"),
            (_src(lat="95", lon="10"), "latitude"),
            (_src(lat="10", lon="-190"), "latitude"),
            (_src(lat="north", lon="10"), "numbers"),
            (_src(lang="english"), "language"),
        ]
        for raw, expected in cases:
            source, err = custom_sources.clean(raw)
            self.assertIsNone(source, raw)
            self.assertIn(expected, err, raw)

    def test_keeps_rounding_country_and_language(self):
        source, _ = custom_sources.clean(_src(country=" usa ", lat="38.897678", lon="-77.036543", lang="EN"))
        self.assertEqual(source["country"], "USA")
        self.assertEqual(source["lat"], 38.8977)
        self.assertEqual(source["lon"], -77.0365)
        self.assertEqual(source["lang"], "en")


class NormalizeTest(unittest.TestCase):
    def test_accepts_list_and_wrapped_shape(self):
        self.assertEqual(len(custom_sources.normalize([_src()])), 1)
        self.assertEqual(len(custom_sources.normalize({"sources": [_src()]})), 1)
        self.assertEqual(custom_sources.normalize(None), [])
        self.assertEqual(custom_sources.normalize({"nope": 1}), [])

    def test_drops_junk_and_duplicate_urls(self):
        rows = custom_sources.normalize([
            _src(),
            _src(name="Alias", url="HTTPS://ALPHA.EXAMPLE/feed/"),
            {"name": "No URL"},
            "junk",
            _src(name="Beta", url="https://beta.example/rss"),
        ])
        self.assertEqual([row["name"] for row in rows], ["Alpha", "Beta"])

    def test_caps_the_list(self):
        rows = custom_sources.normalize(
            [_src(name=f"Source {i}", url=f"https://s{i}.example/feed")
             for i in range(custom_sources.MAX_SOURCES + 5)])
        self.assertEqual(len(rows), custom_sources.MAX_SOURCES)


class OpmlTest(unittest.TestCase):
    def test_reads_nested_outlines_and_reports_bad_rows(self):
        sources, invalid, duplicates = custom_sources.parse_opml(OPML)
        self.assertEqual([s["name"] for s in sources], ["Alpha", "Beta"])
        self.assertEqual(invalid, 1)
        self.assertEqual(duplicates, 0)

    def test_duplicate_urls_count_as_duplicates(self):
        text = """<opml><body>
          <outline text="One" xmlUrl="https://same.example/feed"/>
          <outline text="Two" xmlUrl="https://same.example/feed/"/>
        </body></opml>"""
        sources, invalid, duplicates = custom_sources.parse_opml(text)
        self.assertEqual(len(sources), 1)
        self.assertEqual((invalid, duplicates), (0, 1))

    def test_missing_title_falls_back_to_the_hostname(self):
        sources, _, _ = custom_sources.parse_opml(
            '<opml><body><outline xmlUrl="https://named.example/feed"/></body></opml>')
        self.assertEqual(sources[0]["name"], "named.example")

    def test_namespaced_opml_is_accepted(self):
        text = """<opml xmlns="http://example.com/opml"><body>
          <outline type="rss" text="Namespaced" xmlUrl="https://ns.example/feed"/>
        </body></opml>"""
        sources, _, _ = custom_sources.parse_opml(text)
        self.assertEqual([s["name"] for s in sources], ["Namespaced"])

    def test_junk_and_oversized_documents_are_refused(self):
        self.assertEqual(custom_sources.parse_opml(""), ([], 0, 0))
        with self.assertRaises(ValueError):
            custom_sources.parse_opml("<opml><body>")
        with self.assertRaises(ValueError):
            custom_sources.parse_opml("x" * (custom_sources.MAX_OPML + 1))


class AddMergeRemoveTest(unittest.TestCase):
    def test_add_rejects_builtin_and_existing_names_and_urls(self):
        rows = [_src()]
        _rows, added, err = custom_sources.add(rows, _src(name="ALPHA", url="https://other.example/feed"))
        self.assertIsNone(added)
        self.assertIn("name", err)

        _rows, added, err = custom_sources.add(rows, _src(name="New", url="https://alpha.example/feed/"))
        self.assertIsNone(added)
        self.assertIn("already", err)

        _rows, added, err = custom_sources.add(
            rows, _src(name="Built-in", url="https://builtin.example/feed"),
            reserved_names=["Built-in"], reserved_urls=["https://builtin.example/feed"])
        self.assertIsNone(added)

    def test_add_returns_the_new_list(self):
        rows, added, err = custom_sources.add([], _src(country="USA"))
        self.assertEqual(err, "")
        self.assertEqual(added["name"], "Alpha")
        self.assertEqual([row["name"] for row in rows], ["Alpha"])

    def test_merge_reports_added_duplicates_invalid_and_full(self):
        rows, stats = custom_sources.merge([], [
            _src(),
            _src(name="Dup", url="https://alpha.example/feed/"),
            {"name": "Bad", "url": "ftp://x.example/feed"},
        ], reserved_names=["Built-in"])
        self.assertEqual(stats, {"added": 1, "duplicates": 1, "invalid": 1, "full": 0})
        self.assertEqual(len(rows), 1)

    def test_remove_only_matches_by_name(self):
        rows = [_src(), _src(name="Beta", url="https://beta.example/rss")]
        kept, removed = custom_sources.remove(rows, "alpha")
        self.assertTrue(removed)
        self.assertEqual([row["name"] for row in kept], ["Beta"])
        kept, removed = custom_sources.remove(kept, "Missing")
        self.assertFalse(removed)
        self.assertEqual(len(kept), 1)


class FetcherCustomSourcesTest(unittest.TestCase):
    def test_all_sources_puts_builtins_first_and_marks_custom(self):
        old_path = fetcher.CUSTOM_PATH
        with tempfile.TemporaryDirectory() as tmp:
            fetcher.CUSTOM_PATH = os.path.join(tmp, "custom_sources.json")
            with open(fetcher.CUSTOM_PATH, "w", encoding="utf-8") as fh:
                json.dump([{"name": "My Feed", "url": "https://mine.example/feed",
                            "country": "USA", "lat": 1, "lon": 2}], fh)
            try:
                rows = fetcher.all_sources()
            finally:
                fetcher.CUSTOM_PATH = old_path
        self.assertEqual(rows[:len(fetcher.SOURCES)], fetcher.SOURCES)
        self.assertEqual(rows[-1]["name"], "My Feed")
        self.assertTrue(rows[-1]["custom"])

    def test_a_bad_custom_file_is_ignored(self):
        old_path = fetcher.CUSTOM_PATH
        with tempfile.TemporaryDirectory() as tmp:
            fetcher.CUSTOM_PATH = os.path.join(tmp, "custom_sources.json")
            with open(fetcher.CUSTOM_PATH, "w", encoding="utf-8") as fh:
                fh.write("{not json")
            try:
                self.assertEqual(fetcher._custom_sources(), [])
            finally:
                fetcher.CUSTOM_PATH = old_path


if __name__ == "__main__":
    unittest.main()
