"""The Settings Gemini key: stored outside the app folder, never sent to the page.

These tests write only to a temporary APPDATA folder, so they never touch the
real ``%APPDATA%\\SLATEWIRE`` or a developer's config.py.

Run from the slatewire folder:

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if APP_DIR not in sys.path:
    sys.path.insert(0, APP_DIR)

import app  # noqa: E402
import config  # noqa: E402
import secrets_store  # noqa: E402

GOOD_KEY = "test.gemini-key_0123456789abcdef"
OTHER_KEY = "config.fallback-key_0123456789abcdef"


class SecretsStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"APPDATA": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        patcher = mock.patch.object(config, "GEMINI_API_KEY", secrets_store.PLACEHOLDER)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _file(self):
        return os.path.join(self.tmp.name, secrets_store.APP_FOLDER, secrets_store.FILE_NAME)

    def test_save_creates_the_appdata_folder_and_file(self):
        self.assertFalse(os.path.exists(self._file()))
        self.assertIsNone(secrets_store.save_key(GOOD_KEY))
        self.assertTrue(os.path.isfile(self._file()))
        with open(self._file(), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)[secrets_store.FIELD], GOOD_KEY)
        self.assertEqual(secrets_store.saved_key(), GOOD_KEY)

    def test_surrounding_whitespace_is_trimmed(self):
        self.assertIsNone(secrets_store.save_key("  " + GOOD_KEY + "\n"))
        self.assertEqual(secrets_store.saved_key(), GOOD_KEY)

    def test_saved_key_wins_over_config(self):
        with mock.patch.object(config, "GEMINI_API_KEY", OTHER_KEY):
            self.assertEqual(secrets_store.effective_key(), OTHER_KEY)
            self.assertIsNone(secrets_store.save_key(GOOD_KEY))
            self.assertEqual(secrets_store.effective_key(), GOOD_KEY)
            self.assertEqual(secrets_store.source(), "settings")

    def test_clear_falls_back_to_config_then_to_nothing(self):
        with mock.patch.object(config, "GEMINI_API_KEY", OTHER_KEY):
            self.assertIsNone(secrets_store.save_key(GOOD_KEY))
            self.assertTrue(secrets_store.clear_key())
            self.assertEqual(secrets_store.effective_key(), OTHER_KEY)
            self.assertEqual(secrets_store.source(), "config")
            self.assertTrue(secrets_store.has_key())
        self.assertFalse(secrets_store.clear_key())   # nothing saved any more
        self.assertEqual(secrets_store.source(), "none")
        self.assertFalse(secrets_store.has_key())
        self.assertFalse(os.path.exists(self._file()))

    def test_placeholder_config_is_not_a_key(self):
        with mock.patch.object(config, "GEMINI_API_KEY", secrets_store.PLACEHOLDER):
            self.assertEqual(secrets_store.config_key(), "")
            self.assertEqual(secrets_store.effective_key(), "")
            self.assertEqual(secrets_store.source(), "none")

    def test_invalid_keys_are_rejected_and_not_written(self):
        for bad in ("", "   ", "short", "has a space in it 1234567890",
                    "line\nbreak" + "x" * 40, "x" * (secrets_store.MAX_KEY_LEN + 1),
                    None, 12345):
            self.assertIsInstance(secrets_store.validate(bad), str,
                                  "accepted %r" % (bad,))
            self.assertIsInstance(secrets_store.save_key(bad), str)
        self.assertFalse(os.path.exists(self._file()))

    def test_a_malformed_saved_value_is_ignored(self):
        os.makedirs(os.path.dirname(self._file()), exist_ok=True)
        with open(self._file(), "w", encoding="utf-8") as fh:
            json.dump({secrets_store.FIELD: "not a real key!"}, fh)
        with mock.patch.object(config, "GEMINI_API_KEY", OTHER_KEY):
            self.assertEqual(secrets_store.saved_key(), "")
            self.assertEqual(secrets_store.effective_key(), OTHER_KEY)

    def test_clear_keeps_other_fields_in_the_file(self):
        os.makedirs(os.path.dirname(self._file()), exist_ok=True)
        with open(self._file(), "w", encoding="utf-8") as fh:
            json.dump({secrets_store.FIELD: GOOD_KEY, "something_else": 1}, fh)
        self.assertTrue(secrets_store.clear_key())
        with open(self._file(), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh), {"something_else": 1})


class GeminiKeyRouteTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = mock.patch.dict(os.environ, {"APPDATA": self.tmp.name})
        self.env.start()
        self.addCleanup(self.env.stop)
        patcher = mock.patch.object(config, "GEMINI_API_KEY", secrets_store.PLACEHOLDER)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = app.app.test_client()
        self.base_url = "http://127.0.0.1:%d" % config.PORT

    def _get(self):
        return self.client.get("/api/gemini-key", base_url=self.base_url)

    def _post(self, body):
        return self.client.post("/api/gemini-key", json=body,
                                headers={"X-Slatewire": "1"},
                                base_url=self.base_url)

    def test_get_reports_state_without_the_key(self):
        self.assertIsNone(secrets_store.save_key(GOOD_KEY))
        resp = self._get()
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload["configured"])
        self.assertEqual(payload["source"], "settings")
        self.assertNotIn(GOOD_KEY, resp.get_data(as_text=True))
        self.assertNotIn("key", payload, "GET must not echo a key field")

    def test_post_saves_and_then_clears(self):
        resp = self._post({"key": GOOD_KEY})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.get_json()["configured"])
        self.assertNotIn(GOOD_KEY, resp.get_data(as_text=True))
        self.assertEqual(secrets_store.saved_key(), GOOD_KEY)

        resp = self._post({"clear": True})
        self.assertEqual(resp.status_code, 200)
        payload = resp.get_json()
        self.assertTrue(payload["removed"])
        self.assertFalse(payload["configured"])
        self.assertEqual(payload["source"], "none")

    def test_post_rejects_a_bad_key(self):
        resp = self._post({"key": "short"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("too short", resp.get_json()["error"])
        self.assertEqual(secrets_store.saved_key(), "")

    def test_post_requires_the_app_header(self):
        resp = self.client.post("/api/gemini-key", json={"key": GOOD_KEY},
                                base_url=self.base_url)
        self.assertEqual(resp.status_code, 403)
        self.assertEqual(secrets_store.saved_key(), "")


if __name__ == "__main__":
    unittest.main()
