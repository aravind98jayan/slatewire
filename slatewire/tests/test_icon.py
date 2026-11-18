"""The window/taskbar icon is a real multi-size .ico and is never shown in the page.

Run from the slatewire folder:

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import struct
import unittest

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ICON_PATH = os.path.join(APP_DIR, "slatewire.ico")
PAGE_PATH = os.path.join(APP_DIR, "static", "dashboard.html")


class AppIconTests(unittest.TestCase):
    def test_icon_has_the_sizes_windows_asks_for(self):
        with open(ICON_PATH, "rb") as fh:
            data = fh.read()
        self.assertGreater(len(data), 1000)
        reserved, kind, count = struct.unpack("<HHH", data[:6])
        self.assertEqual((reserved, kind), (0, 1), "not an .ico file")
        self.assertGreaterEqual(count, 4)

        sizes = set()
        for index in range(count):
            entry = data[6 + index * 16:6 + (index + 1) * 16]
            width, height = entry[0], entry[1]
            length, offset = struct.unpack("<II", entry[8:16])
            self.assertEqual(width, height, "icon frames must be square")
            self.assertLessEqual(offset + length, len(data), "frame points past the file")
            sizes.add(256 if width == 0 else width)

        for wanted in (16, 32, 48, 256):
            self.assertIn(wanted, sizes, "missing the %dpx frame" % wanted)

    def test_page_never_shows_the_icon(self):
        with open(PAGE_PATH, encoding="utf-8") as fh:
            page = fh.read()
        self.assertNotIn("slatewire.ico", page)
        self.assertNotIn("slatewire.jpg", page)


if __name__ == "__main__":
    unittest.main()
