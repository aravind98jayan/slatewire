"""Tests for the CISA KEV enrichment layer.

Run from the slatewire folder:

    python -m unittest discover -s tests -v

No network access is used: the downloader is stubbed, so these tests describe
the matching/caching rules and the article schema rather than CISA's uptime.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import kev  # noqa: E402

CATALOG = {
    "CVE-2026-0001": {
        "cve": "CVE-2026-0001", "vendor": "Acme", "product": "Widget",
        "name": "Remote code execution", "dateAdded": "2026-01-01",
        "dueDate": "2026-01-21", "ransomware": False,
    },
    "CVE-2026-0002": {
        "cve": "CVE-2026-0002", "vendor": "Globex", "product": "Server",
        "name": "Authentication bypass", "dateAdded": "2026-02-01",
        "dueDate": "2026-02-21", "ransomware": True,
    },
}


def _article(article_id, title, desc="", source="Example"):
    return {
        "id": article_id,
        "title": title,
        "desc": desc,
        "source": source,
        "published_ts": 1_700_000_000,
    }


class MatchingTest(unittest.TestCase):
    def test_title_cve_is_matched(self):
        a = _article("a", "Acme Widget RCE (CVE-2026-0001) exploited in the wild")
        matches = kev.annotate_article(a, CATALOG)
        self.assertEqual(matches, ["CVE-2026-0001"])
        self.assertTrue(a["kev"])
        self.assertEqual(a["kev_cves"], ["CVE-2026-0001"])
        self.assertEqual(a["kev_details"][0]["vendor"], "Acme")
        self.assertFalse(a["kev_ransomware"])

    def test_description_cve_is_matched(self):
        a = _article("a", "Acme ships an emergency fix", "Tracked as CVE-2026-0001.")
        self.assertEqual(kev.annotate_article(a, CATALOG), ["CVE-2026-0001"])
        self.assertTrue(a["kev"])

    def test_ransomware_flag(self):
        a = _article("a", "Globex Server auth bypass (CVE-2026-0002)")
        kev.annotate_article(a, CATALOG)
        self.assertTrue(a["kev"])
        self.assertTrue(a["kev_ransomware"])

    def test_multiple_cves(self):
        a = _article("a", "CVE-2026-0001 and CVE-2026-0002 are both exploited")
        self.assertEqual(kev.annotate_article(a, CATALOG),
                         ["CVE-2026-0001", "CVE-2026-0002"])
        self.assertTrue(a["kev_ransomware"])

    def test_unknown_cve_is_not_matched(self):
        a = _article("a", "A flaw tracked as CVE-2025-9999")
        self.assertEqual(kev.annotate_article(a, CATALOG), [])
        self.assertFalse(a["kev"])
        self.assertEqual(a["kev_cves"], [])
        self.assertEqual(a["kev_details"], [])
        self.assertFalse(a["kev_ransomware"])

    def test_no_cve_leaves_neutral_fields(self):
        a = _article("a", "A phishing campaign with no CVE at all")
        kev.annotate_article(a, CATALOG)
        self.assertFalse(a["kev"])
        self.assertEqual(a["kev_cves"], [])
        self.assertEqual(a["kev_details"], [])
        self.assertFalse(a["kev_ransomware"])

    def test_lowercase_cve_is_normalised(self):
        a = _article("a", "cve-2026-0001 is being used")
        self.assertEqual(kev.annotate_article(a, CATALOG), ["CVE-2026-0001"])


class EnrichCategoriesTest(unittest.TestCase):
    def test_stats_and_schema(self):
        buckets = {
            "strike": [
                _article("a", "CVE-2026-0001 exploited", "desc"),
                _article("b", "Nothing to see here"),
            ],
            "radar": [
                _article("c", "Globex auth bypass (CVE-2026-0002)"),
            ],
        }
        stats = kev.enrich_categories(buckets, CATALOG)
        self.assertEqual(stats["catalog_size"], 2)
        self.assertEqual(stats["checked_articles"], 3)
        self.assertEqual(stats["matched_articles"], 2)
        self.assertEqual(stats["matched_cves"], 2)
        self.assertEqual(stats["ransomware_articles"], 1)
        self.assertTrue(stats["ok"])
        for items in buckets.values():
            for a in items:
                for field in ("kev", "kev_cves", "kev_details", "kev_ransomware"):
                    self.assertIn(field, a)

    def test_kev_flag_is_shared_with_cluster_representative(self):
        # The visible representative often does not spell out the CVE, while a
        # non-representative member does. The whole story must be flagged.
        rep = _article("rep", "SonicWall warns of an MFA bypass flaw")
        member = _article("k", "CISA adds SonicWall flaw (CVE-2026-0001)")
        rep["cluster_id"] = member["cluster_id"] = "cl_1"
        stats = kev.enrich_categories({"radar": [rep, member]}, CATALOG)
        self.assertTrue(rep["kev"])
        self.assertEqual(rep["kev_cves"], ["CVE-2026-0001"])
        self.assertTrue(member["kev"])
        self.assertEqual(stats["matched_articles"], 1)   # direct hits only
        self.assertEqual(stats["flagged_articles"], 2)   # after propagation
        self.assertEqual(stats["matched_clusters"], 1)

    def test_duplicate_object_is_only_counted_once(self):
        shared = _article("a", "CVE-2026-0001 exploited")
        buckets = {"strike": [shared], "radar": [shared]}
        stats = kev.enrich_categories(buckets, CATALOG)
        self.assertEqual(stats["checked_articles"], 1)
        self.assertEqual(stats["matched_articles"], 1)


class ParseAndCacheTest(unittest.TestCase):
    RAW = {
        "title": "CISA Catalog",
        "vulnerabilities": [
            {
                "cveID": "CVE-2026-0001",
                "vendorProject": "Acme",
                "product": "Widget",
                "vulnerabilityName": "RCE",
                "dateAdded": "2026-01-01",
                "dueDate": "2026-01-21",
                "knownRansomwareCampaignUse": "Unknown",
            },
            {
                "cveID": "CVE-2026-0002",
                "vendorProject": "Globex",
                "product": "Server",
                "vulnerabilityName": "Auth bypass",
                "dateAdded": "2026-02-01",
                "dueDate": "2026-02-21",
                "knownRansomwareCampaignUse": "Known",
            },
            {"cveID": "not-a-cve"},
            "garbage",
        ],
    }

    def test_parse_cisa_schema(self):
        catalog = kev._parse(self.RAW)
        self.assertEqual(set(catalog), {"CVE-2026-0001", "CVE-2026-0002"})
        self.assertFalse(catalog["CVE-2026-0001"]["ransomware"])
        self.assertTrue(catalog["CVE-2026-0002"]["ransomware"])
        self.assertEqual(catalog["CVE-2026-0001"]["dueDate"], "2026-01-21")

    def test_load_catalog_downloads_once_then_uses_cache(self):
        calls = {"n": 0}

        def fake_download():
            calls["n"] += 1
            return self.RAW

        old = kev._download
        kev._download = fake_download
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "kev_cache.json")
                first, meta1 = kev.load_catalog(path=path)
                second, meta2 = kev.load_catalog(path=path)
        finally:
            kev._download = old
        self.assertEqual(calls["n"], 1)
        self.assertEqual(set(first), {"CVE-2026-0001", "CVE-2026-0002"})
        self.assertTrue(meta1["ok"])
        self.assertEqual(set(second), set(first))
        self.assertTrue(meta2["ok"])

    def test_stale_cache_is_used_when_download_fails(self):
        old = kev._download
        kev._download = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "kev_cache.json")
                with open(path, "w", encoding="utf-8") as fh:
                    json.dump({
                        "fetched_at": time.time() - kev.KEV_MAX_AGE - 10,
                        "ok": True,
                        "source": kev.KEV_URL,
                        "vulnerabilities": CATALOG,
                    }, fh)
                catalog, meta = kev.load_catalog(path=path)
        finally:
            kev._download = old
        self.assertEqual(set(catalog), set(CATALOG))
        self.assertFalse(meta["ok"])
        self.assertTrue(meta["stale"])

    def test_offline_never_downloads(self):
        called = {"n": 0}

        def fake_download():
            called["n"] += 1
            return self.RAW

        old = kev._download
        kev._download = fake_download
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "kev_cache.json")
                catalog, meta = kev.load_catalog(path=path, allow_download=False)
        finally:
            kev._download = old
        self.assertEqual(called["n"], 0)
        self.assertEqual(catalog, {})
        self.assertFalse(meta["ok"])

    def test_failed_download_without_cache_does_not_raise(self):
        old = kev._download
        kev._download = lambda: (_ for _ in ()).throw(RuntimeError("offline"))
        try:
            with tempfile.TemporaryDirectory() as tmp:
                path = os.path.join(tmp, "kev_cache.json")
                catalog, meta = kev.load_catalog(path=path)
                self.assertEqual(catalog, {})
                self.assertFalse(meta["ok"])
                self.assertTrue(os.path.exists(path))   # failure is remembered briefly
        finally:
            kev._download = old


if __name__ == "__main__":
    unittest.main()
