"""Tests for article_text.extract_excerpt -- the non-AI brief fallback.

The bug these lock down: a page's <script> block can contain a literal "<p>"
(template strings, document.write, JSON payloads). The paragraph matcher used
to see those as article paragraphs, so raw JavaScript showed up on screen as a
"source excerpt". The same happened to <style> blocks and to code that a site
pre-escaped into the text.

Run from the slatewire folder:

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest import mock

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import article_text  # noqa: E402


def _excerpt(html: str, max_chars: int = 1500) -> str | None:
    """Run extract_excerpt against a fixed page body (no network)."""
    body = html.encode("utf-8")
    with mock.patch.object(article_text.safe_io, "download_limited", return_value=(body, "utf-8")):
        return article_text.extract_excerpt("https://example.test/story", max_chars)


def _excerpt_bytes(body: bytes, enc, max_chars: int = 1500) -> str | None:
    """Same, but with an exact byte body and the charset the header claimed."""
    with mock.patch.object(article_text.safe_io, "download_limited", return_value=(body, enc)):
        return article_text.extract_excerpt("https://example.test/story", max_chars)


# Three real article paragraphs. The first two are eaten by
# SKIP_LEADING_PARAGRAPHS (cookie/nav boilerplate), so a working extractor
# keeps the third -- and every test looks for that one.
P1 = ("<p>Security researchers traced the intrusion back to a single exposed VPN appliance "
      "that had not been patched since the summer.</p>")
P2 = ("<p>The operators moved laterally for weeks without being detected, according to the "
      "incident response firm that handled the case.</p>")
P3 = ("<p>The vendor has published patches for every affected version and urges admins to "
      "apply them before the weekend.</p>")
GOOD = P1 + P2 + P3
MARK = "published patches"

# Two more real paragraphs, so a paragraph under test is not the first one the
# extractor sees (the first two are intentionally skipped as nav/boilerplate).
LEAD = ("<p>Ransomware activity climbed again this quarter, with manufacturing and logistics "
        "firms accounting for most of the reported victims worldwide.</p>"
        "<p>Analysts expect the trend to continue while initial access brokers keep selling "
        "credentials harvested from unpatched edge devices.</p>")

# What a <p>-in-<script> page actually looked like: JavaScript with a <p> in it.
JUNK_SCRIPT = "<script>document.write('<p>window.__cfg={a:1};</p>');</script>"


class ScriptBlockTest(unittest.TestCase):
    def test_paragraph_inside_script_is_not_extracted(self):
        text = _excerpt("<html><body>" + JUNK_SCRIPT + GOOD + "</body></html>") or ""
        self.assertIn(MARK, text)
        self.assertNotIn("__cfg", text)

    def test_paragraph_inside_style_is_not_extracted(self):
        text = _excerpt("<style>/* <p>p{margin:0}</p> */</style>" + GOOD) or ""
        self.assertIn(MARK, text)
        self.assertNotIn("margin", text)

    def test_paragraph_inside_noscript_is_not_extracted(self):
        html = "<noscript><p>Please enable JavaScript (this site needs it) to continue reading.</p></noscript>" + GOOD
        text = _excerpt(html) or ""
        self.assertNotIn("enable JavaScript", text)
        self.assertIn(MARK, text)

    def test_html_comment_is_ignored(self):
        html = "<!-- <p>Draft copy that should never ship to readers at all.</p> -->" + GOOD
        text = _excerpt(html) or ""
        self.assertNotIn("Draft copy", text)
        self.assertIn(MARK, text)


class CodeParagraphTest(unittest.TestCase):
    def test_javascript_paragraph_is_dropped(self):
        html = "<p>var cfg = {mode: 'dark'}; function boot() { return cfg.mode === 'dark'; }</p>" + GOOD
        text = _excerpt(html) or ""
        self.assertNotIn("cfg.mode", text)
        self.assertIn(MARK, text)

    def test_leftover_markup_is_dropped(self):
        html = "<p>The value &lt;script src='x.js'&gt; was injected before rendering happened.</p>" + GOOD
        text = _excerpt(html) or ""
        self.assertNotIn("x.js", text)
        self.assertIn(MARK, text)

    def test_prose_ending_in_a_period_named_document_survives(self):
        html = LEAD + ("<p>Analysts read every line of the incident document. It described how the "
                       "attackers moved laterally for weeks without being detected by the team.</p>") + GOOD
        text = _excerpt(html) or ""
        self.assertIn("incident document", text)

    def test_prose_containing_a_link_survives(self):
        html = LEAD + ("<p>The advisory is available at https://example.test/advisory?id=42 for anyone "
                       "who wants the full technical detail behind the patch.</p>") + GOOD
        text = _excerpt(html) or ""
        self.assertIn("https://example.test/advisory?id=42", text)


class NormalExtractionTest(unittest.TestCase):
    def test_leading_boilerplate_is_skipped(self):
        html = ("<p>We use cookies to improve your experience on this website. Accept all cookies "
                "to continue browsing our site as normal.</p>"
                "<p>Sign up for our newsletter and get the latest headlines in your inbox every "
                "morning, free of charge.</p>" + GOOD)
        text = _excerpt(html) or ""
        self.assertNotIn("cookies", text)
        self.assertIn(MARK, text)

    def test_excerpt_is_capped(self):
        html = ("<p>" + "word " * 800 + ".</p>") * 3
        text = _excerpt(html, max_chars=200) or ""
        self.assertLessEqual(len(text), 201)
        self.assertTrue(text.endswith("\u2026"))


class EncodingTest(unittest.TestCase):
    """The non-AI excerpt must survive pages whose charset is only in <meta>."""

    CZECH = "Členové"
    MOJIBAKE = "Älženov"
    LEAD_CZ = ("<p>This website uses cookies and similar technologies to improve your browsing "
               "experience on every page.</p>"
               "<p>Subscribe to our newsletter for the latest headlines delivered to your inbox "
               "every morning.</p>")
    CZ_BODY = ("<p>Členové NÚKIBu varují před ransomwarovými útoky na české organizace "
               "a doporučují okamžitě přijmout obranná opatření.</p>")

    def _page(self, encoding: str, meta: str = "") -> bytes:
        return (meta + self.LEAD_CZ + self.CZ_BODY).encode(encoding)

    def test_utf8_page_labelled_iso_8859_1_is_not_mangled(self):
        text = _excerpt_bytes(self._page("utf-8"), "ISO-8859-1") or ""
        self.assertIn(self.CZECH, text)
        self.assertNotIn(self.MOJIBAKE, text)

    def test_meta_charset_wins_over_the_iso_default(self):
        body = self._page("utf-8", '<meta charset="utf-8">')
        text = _excerpt_bytes(body, "ISO-8859-1") or ""
        self.assertIn(self.CZECH, text)

    def test_windows_1250_page_uses_its_declared_charset(self):
        text = _excerpt_bytes(self._page("cp1250"), "windows-1250") or ""
        self.assertIn(self.CZECH, text)

    def test_windows_1250_meta_works_with_a_generic_header(self):
        body = self._page("cp1250", '<meta charset="windows-1250">')
        text = _excerpt_bytes(body, "ISO-8859-1") or ""
        self.assertIn(self.CZECH, text)

    def test_utf8_page_without_any_charset(self):
        text = _excerpt_bytes(self._page("utf-8"), None) or ""
        self.assertIn(self.CZECH, text)

    def test_true_latin1_page_still_decodes(self):
        body = (self.LEAD_CZ + "<p>Café owners said the ransomware group demanded payment "
                              "before the end of the week.</p>").encode("latin-1")
        text = _excerpt_bytes(body, "ISO-8859-1") or ""
        self.assertIn("Café", text)


if __name__ == "__main__":
    unittest.main()
