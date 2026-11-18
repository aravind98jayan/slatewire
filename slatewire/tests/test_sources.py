"""The static RSS source list and the globe must stay in sync.

Removing the last source for a country retires its globe marker, so the
countries in `MAP_SOURCES` (dashboard.html) must exactly match the countries
that still appear in `fetcher.SOURCES`.

Run from the slatewire folder:

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import re
import sys
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import fetcher  # noqa: E402

DASHBOARD = os.path.join(APP_DIR, "static", "dashboard.html")


def globe_tags() -> set[str]:
    with open(DASHBOARD, encoding="utf-8") as fh:
        html = fh.read()
    block = re.search(r"const MAP_SOURCES = \[(.*?)\];", html, re.S)
    if not block:
        raise AssertionError("MAP_SOURCES not found in dashboard.html")
    return set(re.findall(r"tag:\s*'([A-Z]+)'", block.group(1)))


class SourceListTests(unittest.TestCase):
    def test_each_source_has_the_fields_the_fetcher_and_globe_need(self):
        for source in fetcher.SOURCES:
            self.assertTrue(source["name"], source)
            self.assertTrue(source["url"].startswith("https://"), source["url"])
            self.assertTrue(source["country"], source)
            self.assertTrue(-90 <= source["lat"] <= 90, source)
            self.assertTrue(-180 <= source["lon"] <= 180, source)

    def test_names_and_urls_are_unique(self):
        names = [source["name"] for source in fetcher.SOURCES]
        urls = [source["url"] for source in fetcher.SOURCES]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(len(urls), len(set(urls)))

    def test_globe_tags_match_the_countries_that_still_have_sources(self):
        self.assertEqual(globe_tags(), {source["country"] for source in fetcher.SOURCES})
