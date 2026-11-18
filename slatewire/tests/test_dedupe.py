"""Tests for the same-story clustering layer (dedupe v1).

Run from the slatewire folder:

    python -m unittest discover -s tests -v

The validation set is a real 103-article fetch, labelled by hand. The test
clusters the whole corpus (so the IDF weights are realistic) and checks that
the labelled same-story pairs end up together while the different-story pairs
stay apart. No network access is required.
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

import dedupe  # noqa: E402
import fetcher  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))


def _article(article_id, title, source, ts=1_700_000_000, desc=""):
    return {
        "id": article_id,
        "title": title,
        "source": source,
        "published_ts": ts,
        "pubDate": "Mon, 09 Nov 2026 10:00:00 GMT",
        "link": "https://example.test/" + article_id,
        "desc": desc,
        "category": "radar",
        "severity": "high",
    }


class ValidationSetTest(unittest.TestCase):
    """The labelled real-fetch set should keep high precision and useful recall."""

    @classmethod
    def setUpClass(cls):
        with open(os.path.join(HERE, "validation_pairs.json"), encoding="utf-8") as fh:
            cls.data = json.load(fh)
        cls.corpus = [dict(a) for a in cls.data["corpus"]]
        dedupe.annotate(cls.corpus)
        cls.by_id = {a["id"]: a for a in cls.corpus}

    def test_precision_and_recall(self):
        tp = fp = fn = tn = 0
        misses = []
        for pair in self.data["pairs"]:
            predicted = self.by_id[pair["a_id"]]["cluster_id"] == self.by_id[pair["b_id"]]["cluster_id"]
            expected = bool(pair["same_story"])
            if expected and predicted:
                tp += 1
            elif expected:
                fn += 1
                misses.append(pair["id"])
            elif predicted:
                fp += 1
                misses.append(pair["id"])
            else:
                tn += 1
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        self.assertGreaterEqual(precision, 0.95, "precision dropped: %r" % (misses,))
        self.assertGreaterEqual(recall, 0.60, "recall dropped: %r" % (misses,))

    def test_every_article_stays_in_the_corpus(self):
        self.assertEqual(len(self.data["corpus"]), len(self.corpus))
        self.assertEqual({a["id"] for a in self.data["corpus"]}, set(self.by_id))

    def test_validation_clusters_have_representatives(self):
        for article in self.corpus:
            self.assertIn("cluster_id", article)
            self.assertIn("is_representative", article)
            self.assertIn("dupe_count", article)
            self.assertIn("source_count", article)
            self.assertIn("also_reported_by", article)
        reps = [a for a in self.corpus if a["is_representative"]]
        self.assertEqual(len(reps), len({a["cluster_id"] for a in self.corpus}))


class ClusteringRulesTest(unittest.TestCase):
    def test_template_advisories_do_not_merge(self):
        items = [
            _article("a", "Security Alert (A26-11-08): Multiple Vulnerabilities in Citrix Products", "GovCERT.HK"),
            _article("b", "Security Alert (A26-11-07): Multiple Vulnerabilities in Cisco Products", "GovCERT.HK"),
            _article("c", "Security Alert (A26-11-10): Multiple Vulnerabilities in Microsoft Edge", "GovCERT.HK"),
        ]
        groups = dedupe.cluster_articles(items)
        self.assertEqual(len(groups), 3)

    def test_newsletter_issues_do_not_merge(self):
        items = [
            _article("a", "SECURITY AFFAIRS MALWARE NEWSLETTER ROUND 122", "Security Affairs"),
            _article("b", "SECURITY AFFAIRS AI-CYBERSECURITY NEWSLETTER ROUND 7", "Security Affairs"),
        ]
        self.assertEqual(len(dedupe.cluster_articles(items)), 2)

    def test_shared_cve_is_a_strong_signal(self):
        pending = [
            _article("a", "CVE-2026-19868 added to the KEV catalog", "CISA", 1_700_000_000),
            _article("b", "SonicWall patches authentication bypass (CVE-2026-19868)", "Vendor", 1_700_003_600),
        ]
        groups = dedupe.cluster_articles(pending)
        self.assertEqual(len(groups), 1)

    def test_publication_window_is_a_hard_gate(self):
        title_a = "OpenAI launches GPT-6 Cyber after finding Firefox flaws"
        title_b = "OpenAI releases GPT-6 Cyber with Firefox fixes"
        close = [_article("a", title_a, "A", 1_700_000_000),
                 _article("b", title_b, "B", 1_700_000_000 + 71 * 3600)]
        far = [_article("a", title_a, "A", 1_700_000_000),
               _article("b", title_b, "B", 1_700_000_000 + 73 * 3600)]
        self.assertEqual(len(dedupe.cluster_articles(close)), 1)
        self.assertEqual(len(dedupe.cluster_articles(far)), 2)

    def test_representative_is_earliest_then_most_complete(self):
        items = [
            _article("later", "OpenAI launches GPT-6 Cyber after finding Firefox flaws", "B", 1_700_100_000,
                     "A much longer description of the same announcement, with many more details."),
            _article("first", "OpenAI releases GPT-6 Cyber with Firefox fixes", "A", 1_700_000_000, "Short."),
        ]
        clusters, _stats = dedupe.annotate(items)
        self.assertEqual(len(clusters), 1)
        self.assertTrue(next(a for a in items if a["id"] == "first")["is_representative"])
        self.assertFalse(next(a for a in items if a["id"] == "later")["is_representative"])

    def test_singleton_gets_neutral_fields(self):
        item = _article("only", "A lone story about a new phishing campaign", "Example")
        clusters, stats = dedupe.annotate([item])
        self.assertEqual(clusters, {})
        self.assertEqual(stats["duplicates"], 0)
        self.assertTrue(item["is_representative"])
        self.assertEqual(item["dupe_count"], 0)
        self.assertEqual(item["source_count"], 1)
        self.assertEqual(item["also_reported_by"], [])

    def test_annotate_is_idempotent(self):
        items = [
            _article("a", "OpenAI launches GPT-6 Cyber after finding Firefox flaws", "A", 1_700_000_000),
            _article("b", "OpenAI releases GPT-6 Cyber with Firefox fixes", "B", 1_700_000_100),
        ]
        first, _ = dedupe.annotate(items)
        ids = {a["id"]: a["cluster_id"] for a in items}
        second, _ = dedupe.annotate(items)
        self.assertEqual(first, second)
        self.assertEqual(ids, {a["id"]: a["cluster_id"] for a in items})

    def test_accent_folding_keeps_words_comparable(self):
        self.assertEqual(dedupe._tokens("Kühlsystemen für Angriffe"), dedupe._tokens("Kuhlsystemen fur Angriffe"))


class CategoriesSchemaTest(unittest.TestCase):
    def test_apply_to_categories_keeps_shape_and_adds_fields(self):
        buckets = {
            "strike": [
                _article("a", "OpenAI launches GPT-6 Cyber after finding Firefox flaws", "A", 1_700_000_000),
                _article("b", "OpenAI releases GPT-6 Cyber with Firefox fixes", "B", 1_700_000_100),
            ],
            "radar": [
                _article("c", "Cisco patches a critical IOS XE flaw", "C", 1_700_000_200),
            ],
        }
        before_ids = {cat: [a["id"] for a in items] for cat, items in buckets.items()}
        clusters, stats = dedupe.apply_to_categories(buckets)
        self.assertEqual(set(buckets), {"strike", "radar"})
        self.assertEqual({cat: [a["id"] for a in items] for cat, items in buckets.items()}, before_ids)
        self.assertEqual(len(clusters), 1)
        self.assertEqual(stats["duplicates"], 1)
        self.assertEqual(buckets["strike"][0]["source_count"], 2)
        self.assertEqual(len(buckets["strike"][0]["also_reported_by"]), 1)
        self.assertEqual(buckets["radar"][0]["dupe_count"], 0)


class FetchIntegrationTest(unittest.TestCase):
    """fetch_all() should call the post-processing stage and expose the new keys."""

    def test_fetch_all_adds_clusters(self):
        old_collect = fetcher.collect_fresh
        old_history = fetcher.HISTORY_PATH
        old_load = fetcher._load_history
        old_kev = fetcher.kev.load_catalog
        now = time.time()
        fresh = [
            _article("a", "OpenAI launches GPT-6 Cyber after finding Firefox flaws", "A", now),
            _article("b", "OpenAI releases GPT-6 Cyber with Firefox fixes", "B", now + 100),
        ]
        try:
            with tempfile.TemporaryDirectory() as tmp:
                fetcher.HISTORY_PATH = os.path.join(tmp, "history.json")
                fetcher.collect_fresh = lambda: (fresh, [], [])
                fetcher._load_history = lambda path=None: {}
                # keep the test offline: no CISA download
                fetcher.kev.load_catalog = lambda **kw: ({}, {"ok": False, "stale": False,
                                                               "fetched_at": 0, "source": ""})
                data = fetcher.fetch_all()
        finally:
            fetcher.collect_fresh = old_collect
            fetcher.HISTORY_PATH = old_history
            fetcher._load_history = old_load
            fetcher.kev.load_catalog = old_kev

        self.assertIn("clusters", data)
        self.assertIn("dedupe", data)
        self.assertIn("kev", data)
        self.assertEqual(set(data["categories"]), set(fetcher.CATEGORIES))
        articles = [a for items in data["categories"].values() for a in items]
        self.assertEqual(len(articles), 2)
        self.assertTrue(all("is_representative" in a for a in articles))
        self.assertEqual(len(data["clusters"]), 1)
        self.assertEqual(data["dedupe"]["duplicates"], 1)


if __name__ == "__main__":
    unittest.main()
