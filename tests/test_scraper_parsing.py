# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
#
# This file is part of apkforge and licensed under the GNU GPLv3.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

"""Parsing tests for the scrapers: the layer that breaks when a source redesigns."""

import json
import unittest

from src.scrapers.apkmirror import APKMirrorError, APKMirrorScraper
from src.scrapers.base import _parse_html
from src.scrapers.direct import DirectScraper, DirectScraperError
from src.scrapers.github import GitHubScraper

_VARIANT_ROWS = """
<div id="primary">
  <div class="table-row headerFont"><div class="table-cell">Variant</div><div class="table-cell">Arch</div>
    <div class="table-cell">Min</div><div class="table-cell">DPI</div></div>
  <div class="table-row headerFont">
    <div class="table-cell"><a href="/apk/x/y-release/a-bundle/"><span class="apkm-badge">BUNDLE</span></a></div>
    <div class="table-cell">arm64-v8a</div><div class="table-cell">5.0+</div><div class="table-cell">nodpi</div></div>
  <div class="table-row headerFont">
    <div class="table-cell"><a href="/apk/x/y-release/a-apk/"><span class="apkm-badge">APK</span></a></div>
    <div class="table-cell">arm64-v8a</div><div class="table-cell">5.0+</div><div class="table-cell">nodpi</div></div>
  <div class="table-row headerFont">
    <div class="table-cell"><a href="/apk/x/y-release/other-apk/"><span class="apkm-badge">APK</span></a></div>
    <div class="table-cell">armeabi-v7a</div><div class="table-cell">5.0+</div><div class="table-cell">nodpi</div></div>
</div>
"""


class APKMirrorVariantTests(unittest.TestCase):
    def setUp(self) -> None:
        self.scraper = object.__new__(APKMirrorScraper)

    def test_prefers_plain_apk_over_bundle_for_target_arch(self) -> None:
        picked = self.scraper._pick_variant(_parse_html(_VARIANT_ROWS), "", "arm64-v8a")
        assert picked is not None
        url, kind = picked
        self.assertEqual(kind, "APK")
        self.assertTrue(url.endswith("/a-apk/"))

    def test_other_arch_is_not_picked(self) -> None:
        picked = self.scraper._pick_variant(_parse_html(_VARIANT_ROWS), "", "x86_64")
        self.assertIsNone(picked)

    def test_universal_arch_is_accepted_for_any_target(self) -> None:
        html = _VARIANT_ROWS.replace("arm64-v8a", "universal")
        picked = self.scraper._pick_variant(_parse_html(html), "", "arm64-v8a")
        self.assertIsNotNone(picked)

    def test_category_is_derived_from_the_url(self) -> None:
        self.assertEqual(APKMirrorScraper._category_of("https://www.apkmirror.com/apk/whatsapp-inc/whatsapp/"), "whatsapp")


class APKMirrorUnspecificVersionTests(unittest.TestCase):
    """Searching APKMirror for the literal "latest" can never match a release."""

    def _scraper(self, release_urls: dict[str, str]):
        scraper = object.__new__(APKMirrorScraper)
        scraper._cache = {}
        scraper._category = "whatsapp-business"
        scraper._release_urls = dict(release_urls)
        scraper.requested = []

        def _get(url, headers=None):
            scraper.requested.append(url)
            if "-release/" in url:
                return '<a class="btn" href="/dl/step">dl</a>'
            if "/download/" in url or "type=apk" in url:
                return '<span><a rel="nofollow" href="/final.apk">x</a></span>'
            return '<span><a rel="nofollow" href="/final.apk">x</a></span>'

        scraper.net = type("N", (), {
            "get": staticmethod(_get),
            "download": staticmethod(lambda url, dest, headers=None: dest.write_bytes(b"PK\x03\x04")),
        })()
        return scraper

    def test_unspecific_version_uses_the_newest_release(self) -> None:
        import tempfile
        from pathlib import Path
        scraper = self._scraper({
            "2.26.36.10": "https://www.apkmirror.com/apk/w/whatsapp-business-2-26-36-10-release/",
            "2.26.37.5": "https://www.apkmirror.com/apk/w/whatsapp-business-2-26-37-5-release/",
        })
        with tempfile.TemporaryDirectory() as tmp:
            scraper.download("https://www.apkmirror.com/apk/whatsapp-inc/whatsapp-business/", "latest",
                             Path(tmp) / "com.whatsapp.w4b-vlatest-arm64-v8a.apk", "arm64-v8a", "")
        self.assertTrue(any("2-26-37-5-release" in u for u in scraper.requested))
        self.assertFalse(any("?s=" in u for u in scraper.requested), "must not search for a literal version")

    def test_unspecific_version_detection(self) -> None:
        for v in ("latest", "auto", "nightly", "", "LATEST"):
            self.assertTrue(APKMirrorScraper._is_unspecific(v), v)
        for v in ("2.26.37.74", "v1.2.3", "11.0.0"):
            self.assertFalse(APKMirrorScraper._is_unspecific(v), v)

    def test_concrete_version_still_uses_the_search(self) -> None:
        import tempfile
        from pathlib import Path
        scraper = self._scraper({})
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(APKMirrorError):
            scraper.download("https://www.apkmirror.com/apk/whatsapp-inc/whatsapp-business/", "2.26.37.74",
                             Path(tmp) / "x.apk", "arm64-v8a", "")
        self.assertTrue(any("?s=2.26.37.74" in u for u in scraper.requested))


class GitHubAssetTests(unittest.TestCase):
    def _scraper(self, assets: list[dict]) -> GitHubScraper:
        scraper = object.__new__(GitHubScraper)
        scraper._assets = assets
        scraper._cache = {}
        return scraper

    def test_metadata_derives_versions_from_asset_names(self) -> None:
        # Assets are named "<release name>-<version>-<arch>.<ext>"; both arches of one
        # version must collapse to a single version entry.
        release = {"name": "com.example.app", "assets": [
            {"name": "com.example.app-1.2.3-arm64-v8a.apk"},
            {"name": "com.example.app-1.2.3-armeabi-v7a.apk"},
            {"name": "com.example.app-1.2.4-arm64-v8a.apkm"},
            {"name": "notes.txt"},
        ]}
        scraper = self._scraper([])
        scraper.net = type("N", (), {"gh_headers": {}, "get": lambda self, u, headers=None: json.dumps(release)})()
        meta = scraper.fetch_metadata("https://github.com/o/r/releases/tag/com.example.app")
        self.assertEqual(meta.pkg_name, "com.example.app")
        self.assertEqual(meta.versions, ["1.2.3", "1.2.4"])

    def test_metadata_falls_back_to_the_tag_when_no_apk_assets(self) -> None:
        release = {"name": "nightly", "assets": [{"name": "notes.txt"}]}
        scraper = self._scraper([])
        scraper.net = type("N", (), {"gh_headers": {}, "get": lambda self, u, headers=None: json.dumps(release)})()
        meta = scraper.fetch_metadata("https://github.com/o/r/releases/tag/nightly")
        self.assertEqual(meta.versions, ["nightly"])

    def test_arch_specific_asset_is_selected(self) -> None:
        assets = [
            {"name": "app-1.2.3-armeabi-v7a.apk", "browser_download_url": "u-v7a"},
            {"name": "app-1.2.3-arm64-v8a.apk", "browser_download_url": "u-v8a"},
        ]
        scraper = self._scraper(assets)
        picked = []
        scraper.net = type("N", (), {
            "gh_headers": {},
            "download": lambda self, url, dest, headers=None: (picked.append(url), dest.write_bytes(b"PK\x03\x04")),
        })()
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            res = scraper.download("https://github.com/o/r/releases/tag/1.2.3", "1.2.3", Path(tmp) / "p-v1.2.3-arm64-v8a.apk", "arm64-v8a", "")
        self.assertEqual(picked, ["u-v8a"])
        self.assertFalse(res.is_bundle)
        self.assertEqual(res.original_name, "app-1.2.3-arm64-v8a.apk")

    def test_bundle_asset_is_flagged(self) -> None:
        scraper = self._scraper([{"name": "app-1.2.3-arm64-v8a.apkm", "browser_download_url": "u"}])
        scraper.net = type("N", (), {
            "gh_headers": {},
            "download": lambda self, url, dest, headers=None: dest.write_bytes(b"PK\x03\x04"),
        })()
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            res = scraper.download("https://github.com/o/r/releases/tag/1.2.3", "1.2.3", Path(tmp) / "p-v1.2.3-arm64-v8a.apk", "arm64-v8a", "")
        self.assertTrue(res.is_bundle)
        self.assertTrue(res.path.name.endswith(".apkm"))


class DirectLinkTests(unittest.TestCase):
    def _scraper(self, html: str) -> DirectScraper:
        scraper = object.__new__(DirectScraper)
        scraper._direct_urls = {}
        scraper._cache = {}
        scraper.net = type("N", (), {"get": lambda self, u, headers=None: html})()
        return scraper

    def test_anchor_link_is_found_and_resolved(self) -> None:
        scraper = self._scraper('<a href="/dl/app-1.2.3.apk">download</a>')
        meta = scraper.fetch_metadata("https://example.com/downloads")
        self.assertEqual(meta.versions, ["1.2.3"])
        self.assertEqual(scraper._direct_urls["https://example.com/downloads"], "https://example.com/dl/app-1.2.3.apk")

    def test_embedded_url_is_found_when_no_anchor_exists(self) -> None:
        scraper = self._scraper('<script>var u = "https://cdn.example.com/a/WhatsApp.apk?t=1";</script>')
        meta = scraper.fetch_metadata("https://example.com/downloads")
        self.assertEqual(meta.versions, ["latest"])
        self.assertTrue(scraper._direct_urls["https://example.com/downloads"].endswith("WhatsApp.apk?t=1"))

    def test_page_without_any_apk_link_raises(self) -> None:
        scraper = self._scraper("<p>nothing here</p>")
        with self.assertRaises(DirectScraperError):
            scraper.fetch_metadata("https://example.com/downloads")

    def test_bundle_extension_is_detected(self) -> None:
        scraper = self._scraper('<a href="https://cdn.example.com/app-2.0.0.xapk">get</a>')
        scraper.fetch_metadata("https://example.com/d")
        import tempfile
        from pathlib import Path
        scraper.net = type("N", (), {"download": lambda self, url, dest, headers=None: dest.write_bytes(b"PK\x03\x04")})()
        with tempfile.TemporaryDirectory() as tmp:
            res = scraper.download("https://example.com/d", "2.0.0", Path(tmp) / "p-v2.0.0-all.apk", "all", "")
        self.assertTrue(res.is_bundle)
        self.assertTrue(res.path.name.endswith(".apkm"))


if __name__ == "__main__":
    unittest.main()
