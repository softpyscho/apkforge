# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
#
# This file is part of apkforge and licensed under the GNU GPLv3.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import tempfile
import unittest
from pathlib import Path

from src.scrapers.base import make_scraper
from src.scrapers.direct import DirectScraper


class _FakeNet:
    def download(self, url: str, path: Path) -> None:
        path.write_bytes(b"PK\x03\x04fake")


class MakeScraperTests(unittest.TestCase):
    def test_known_sources(self) -> None:
        for source in ("apkmirror", "github", "uptodown", "direct"):
            scraper = make_scraper(source, object())  # type: ignore[arg-type]
            self.assertIsNotNone(scraper)

    def test_unknown_source_raises(self) -> None:
        with self.assertRaises(ValueError):
            make_scraper("bogus", object())  # type: ignore[arg-type]

    def test_apkpure_is_no_longer_supported(self) -> None:
        with self.assertRaises(ValueError):
            make_scraper("apkpure", object())  # type: ignore[arg-type]


class DirectScraperTests(unittest.TestCase):
    def test_version_less_url_is_accepted(self) -> None:
        # Regression: official vendor links (e.g. WhatsApp) have no version in the URL.
        scraper = object.__new__(DirectScraper)
        scraper._direct_urls = {"page": "https://cdn.example.com/WhatsApp.apk?token=abc"}
        scraper.net = _FakeNet()  # type: ignore[assignment]
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "com.whatsapp-v2.26.35.71-arm64-v8a.apk"
            result = scraper.download("page", "2.26.35.71", dest, "arm64-v8a", "")
        self.assertEqual(result.path, dest)
        self.assertFalse(result.is_bundle)
        self.assertEqual(result.original_name, "WhatsApp.apk")


if __name__ == "__main__":
    unittest.main()