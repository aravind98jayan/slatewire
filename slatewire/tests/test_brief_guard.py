"""Guard rails for AI briefs.

The bug these lock down: when a source page had no article text (podcasts,
copyright-only pages), Gemini answered *about the prompt* -- "The provided
article text is empty..." -- and that reply was stored as a real brief and
shown in the card. These helpers reject such replies, spot podcast items and
recognize licence-only text.

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

import app  # noqa: E402


class BriefGuardTests(unittest.TestCase):
    def test_meta_reply_is_rejected(self):
        self.assertTrue(app._brief_is_unusable(
            "The provided article text is empty and contains no news content. "
            "It only includes copyright information and a link. Please provide "
            "the full text so it can be summarized for your briefing app."))
        self.assertTrue(app._brief_is_unusable("I'm unable to summarize this."))
        self.assertFalse(app._brief_is_unusable(
            "A London hotel left its Booking.com administrative username and "
            "password written on a whiteboard behind the front desk."))

    def test_licence_only_text_is_not_article_text(self):
        self.assertFalse(app._has_article_text(
            "(c) SANS Internet Storm Center. https://isc.sans.edu Creative "
            "Commons Attribution-Noncommercial 3.0 United States License."))
        self.assertTrue(app._has_article_text(
            "A London hotel left its Booking.com administrative username and "
            "password written on a whiteboard in a back room behind the front "
            "desk. Anyone near the desk could easily see the credentials or "
            "take a photo of them."))

    def test_podcast_detected_from_title_or_link(self):
        self.assertTrue(app._looks_like_podcast(
            {"title": "ISC Stormcast https://isc.sans.edu/podcastdetail/10176"}))
        self.assertTrue(app._looks_like_podcast(
            {"title": "Episode 4", "link": "https://example.com/podcast/4"}))
        self.assertFalse(app._looks_like_podcast(
            {"title": "Ransomware report", "link": "https://example.com/a"}))


if __name__ == "__main__":
    unittest.main()
