# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
#
# This file is part of apkforge and licensed under the GNU GPLv3.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import unittest
from pathlib import Path

from src.scrapers.base import make_scraper
from src.scrapers.direct import DirectScraper, DirectScraperError


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


class DirectScraperGuardTests(unittest.TestCase):
    def _scraper(self, url: str) -> DirectScraper:
        scraper = object.__new__(DirectScraper)
        scraper._direct_urls = {"page": url}
        return scraper

    def test_rejects_mismatched_version(self) -> None:
        scraper = self._scraper("https://cdn.example.com/app_2.26.34.77.apk")
        with self.assertRaises(DirectScraperError):
            scraper.download("page", "2.26.30.85", Path("out.apk"), "arm64-v8a", "")

    def test_boundary_does_not_match_substring(self) -> None:
        scraper = self._scraper("https://cdn.example.com/app_2.26.300.1.apk")
        with self.assertRaises(DirectScraperError):
            scraper.download("page", "2.26.30", Path("out.apk"), "arm64-v8a", "")


if __name__ == "__main__":
    unittest.main()