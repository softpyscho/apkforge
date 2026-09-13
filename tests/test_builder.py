# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
#
# This file is part of apkforge and licensed under the GNU GPLv3.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from src.core import builder
from src.scrapers.base import AppMetadata


class FakeScraper:
    def __init__(self, versions: list[str]) -> None:
        self._versions = versions

    def cached_metadata(self, url: str) -> AppMetadata:
        return AppMetadata(pkg_name="pkg", versions=list(self._versions))


class FakeEntry:
    def __init__(self, version: str = "1.2.3.xx", dl_urls: dict[str, str] | None = None) -> None:
        self.table = "Sample"
        self.version = version
        self.patches: dict[str, dict] = {}
        self.dl_urls = dl_urls or {"direct": "d", "apkmirror": "a"}


def _write_bundle(path: Path) -> None:
    with zipfile.ZipFile(path, "w") as zf:
        for name in (
            "base.apk",
            "config.en.apk",
            "config.fr.apk",
            "config.arm64_v8a.apk",
            "config.armeabi_v7a.apk",
            "config.xhdpi.apk",
            "config.xxhdpi.apk",
            "metadata.json",
        ):
            zf.writestr(name, b"PK\x03\x04data")


class SanitizeTests(unittest.TestCase):
    def test_sanitize_asset_name(self) -> None:
        self.assertEqual(builder._sanitize_asset_name("app name (v1).apk"), "app.name.v1.apk")


class OptimizeBundleTests(unittest.TestCase):
    def test_keeps_target_abi_and_english_xxhdpi(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src, dst = tmp_path / "in.apkm", tmp_path / "out.apkm"
            _write_bundle(src)
            builder._optimize_bundle(src, dst, "arm64-v8a")
            with zipfile.ZipFile(dst) as zf:
                names = set(zf.namelist())

        self.assertIn("base.apk", names)
        self.assertIn("metadata.json", names)
        self.assertIn("config.en.apk", names)
        self.assertIn("config.xxhdpi.apk", names)
        self.assertIn("config.arm64_v8a.apk", names)
        self.assertNotIn("config.fr.apk", names)
        self.assertNotIn("config.xhdpi.apk", names)
        self.assertNotIn("config.armeabi_v7a.apk", names)

    def test_maybe_optimize_skips_arch_all(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.apkm"
            _write_bundle(src)
            result = builder.DownloadResult(path=src, is_bundle=True, original_name="orig.apkm", source_used="github")
            self.assertIs(builder._maybe_optimize_bundle(result, "all"), result)

    def test_maybe_optimize_preserves_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            src = tmp_path / "in.apkm"
            _write_bundle(src)
            result = builder.DownloadResult(path=src, is_bundle=True, original_name="orig.apkm", source_used="github")
            with mock.patch.object(builder, "TEMP_DIR", tmp_path):
                lean = builder._maybe_optimize_bundle(result, "arm64-v8a")
            self.assertTrue(lean.is_bundle)
            self.assertEqual(lean.original_name, "orig.apkm")
            self.assertEqual(lean.source_used, "github")


class ResolveVersionTests(unittest.TestCase):
    def test_prefers_wildcard_prefix_across_sources(self) -> None:
        scrapers = {
            "direct": FakeScraper(["2.26.34.77"]),
            "apkmirror": FakeScraper(["2.26.34.77", "2.26.30.85", "2.26.30.70"]),
        }
        version, custom = builder._resolve_version(FakeEntry(version="2.26.30.xx"), None, "", "pkg", "direct", scrapers)  # type: ignore[arg-type]
        self.assertEqual(version, "2.26.30.85")
        self.assertTrue(custom)

    def test_wildcard_falls_back_to_latest(self) -> None:
        scrapers = {"direct": FakeScraper(["2.27.1.1"])}
        entry = FakeEntry(version="2.26.30.xx", dl_urls={"direct": "d"})
        version, _ = builder._resolve_version(entry, None, "", "pkg", "direct", scrapers)  # type: ignore[arg-type]
        self.assertEqual(version, "2.27.1.1")

    def test_auto_picks_highest(self) -> None:
        scrapers = {"direct": FakeScraper(["1.0.0", "1.2.0"])}
        entry = FakeEntry(version="auto", dl_urls={"direct": "d"})
        version, custom = builder._resolve_version(entry, None, "", "pkg", "direct", scrapers)  # type: ignore[arg-type]
        self.assertEqual(version, "1.2.0")
        self.assertFalse(custom)


if __name__ == "__main__":
    unittest.main()