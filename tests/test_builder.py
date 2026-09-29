# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
#
# This file is part of apkforge and licensed under the GNU GPLv3.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import contextlib
import json
import os
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


class CachedVersionsTests(unittest.TestCase):
    def test_collects_versions_from_cached_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            (cache / "com.example-v1.2.3-arm64-v8a.apk").write_bytes(b"x")
            (cache / "com.example-v2.0.0-arm64-v8a.apkm").write_bytes(b"x")
            (cache / "com.example-v1.2.3-arm64-v8a.src").write_text("direct", encoding="utf-8")
            (cache / "other-v9.9.9-arm64-v8a.apk").write_bytes(b"x")
            with mock.patch.object(builder, "ORIGINAL_APK_DIR", cache):
                versions = builder._cached_apk_versions("com.example")
        self.assertEqual(sorted(versions), ["1.2.3", "2.0.0"])

    def test_missing_dir_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp) / "missing"):
            self.assertEqual(builder._cached_apk_versions("com.example"), [])

    def test_empty_pkg_name_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp)):
            self.assertEqual(builder._cached_apk_versions(""), [])


class PatchWithRetriesTests(unittest.TestCase):
    def test_returns_output_on_success(self) -> None:
        out = Path("out.apk")
        with mock.patch.object(builder, "_apply_patch", return_value=out):
            apk, excluded, exc = builder._patch_with_retries("entry", "arch", "1.0", False, "patcher", "", "dl")  # type: ignore[arg-type]
        self.assertEqual(apk, out)
        self.assertEqual(excluded, [])
        self.assertIsNone(exc)

    def test_excludes_failing_patch_and_retries(self) -> None:
        calls: list[list[str]] = []

        def _apply(entry, arch, version, force, patcher, list_patches, dl_result, excluded):
            calls.append(list(excluded))
            if not excluded:
                raise builder.BuilderError("FAILED: Bad Patch\nstack")
            return Path("out.apk")

        with mock.patch.object(builder, "_apply_patch", side_effect=_apply):
            apk, excluded, exc = builder._patch_with_retries("entry", "arch", "1.0", False, "patcher", "", "dl")  # type: ignore[arg-type]
        self.assertEqual(apk, Path("out.apk"))
        self.assertEqual(excluded, ["Bad Patch"])
        self.assertEqual(calls, [[], ["Bad Patch"]])
        self.assertIsNone(exc)

    def test_repeated_failure_of_same_patch_stops(self) -> None:
        def _apply(entry, arch, version, force, patcher, list_patches, dl_result, excluded):
            raise builder.BuilderError("FAILED: Bad Patch\nstack")

        with mock.patch.object(builder, "_apply_patch", side_effect=_apply):
            apk, excluded, exc = builder._patch_with_retries("entry", "arch", "1.0", False, "patcher", "", "dl")  # type: ignore[arg-type]
        self.assertIsNone(apk)
        self.assertEqual(excluded, ["Bad Patch"])
        self.assertIn("failed again after being excluded", str(exc))

    def test_non_patch_failure_stops_immediately(self) -> None:
        with mock.patch.object(builder, "_apply_patch", side_effect=builder.PatcherError("boom")):
            apk, excluded, exc = builder._patch_with_retries("entry", "arch", "1.0", False, "patcher", "", "dl")  # type: ignore[arg-type]
        self.assertIsNone(apk)
        self.assertEqual(excluded, [])
        self.assertIn("boom", str(exc))


class MatchesWildcardTests(unittest.TestCase):
    def test_matching_manifest_version(self) -> None:
        with (
            mock.patch.object(builder, "_read_manifest_axml", return_value=b"axml"),
            mock.patch.object(builder, "parse_axml_strings", return_value=["2.26.35.71", "1.0.0"]),
        ):
            self.assertTrue(builder._matches_wildcard(Path("x.apk"), "2.26.35.xx"))
            self.assertFalse(builder._matches_wildcard(Path("x.apk"), "2.26.30.xx"))

    def test_unreadable_manifest_is_accepted(self) -> None:
        with mock.patch.object(builder, "_read_manifest_axml", return_value=None):
            self.assertTrue(builder._matches_wildcard(Path("x.apk"), "2.26.30.xx"))


class VerifyWildcardTests(unittest.TestCase):
    def test_matching_minor_is_verified(self) -> None:
        self.assertTrue(builder._should_verify_wildcard("2.26.35.xx", "2.26.35.71"))

    def test_resolved_latest_is_not_rejected(self) -> None:
        self.assertFalse(builder._should_verify_wildcard("2.26.35.xx", "latest"))

    def test_newer_minor_is_not_rejected(self) -> None:
        self.assertFalse(builder._should_verify_wildcard("2.26.35.xx", "2.26.42.83"))

    def test_non_wildcard_config_is_never_verified(self) -> None:
        self.assertFalse(builder._should_verify_wildcard("2.26.35.0", "2.26.35.74"))
        self.assertFalse(builder._should_verify_wildcard("latest", "latest"))


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


@contextlib.contextmanager
def _in_tmp_cwd():
    cwd = Path.cwd()
    with tempfile.TemporaryDirectory() as tmp:
        os.chdir(tmp)
        try:
            yield Path(tmp)
        finally:
            os.chdir(cwd)


class WriteVersionsInfoTests(unittest.TestCase):
    def test_stale_exclusions_of_rebuilt_app_are_dropped(self) -> None:
        # Regression: a clean rebuild used to leave the previous run's excluded patches
        # in place, so the README and release notes kept striking them through.
        with _in_tmp_cwd() as tmp:
            (tmp / "versions_info.json").write_text(
                json.dumps({"success": [], "excluded_patches": {"Greenify": ["Unlock Donation"], "Greenify (arm64-v8a)": ["Unlock Donation"]}}),
                encoding="utf-8",
            )
            report = {"success": [{"app": "Greenify", "label": "Greenify (arm64-v8a)"}], "failed": [], "excluded_patches": {}}
            builder._write_versions_info(report, {"Greenify", "Greenify (arm64-v8a)"})
            data = json.loads((tmp / "versions_info.json").read_text(encoding="utf-8"))
        self.assertEqual(data["excluded_patches"], {})

    def test_other_apps_keep_their_exclusions(self) -> None:
        with _in_tmp_cwd() as tmp:
            (tmp / "versions_info.json").write_text(
                json.dumps({"success": [], "excluded_patches": {"Reddit": ["Hide ads"]}}),
                encoding="utf-8",
            )
            report = {"success": [], "failed": [], "excluded_patches": {"Greenify": ["Unlock Donation"]}}
            builder._write_versions_info(report, {"Greenify"})
            data = json.loads((tmp / "versions_info.json").read_text(encoding="utf-8"))
        self.assertEqual(data["excluded_patches"], {"Reddit": ["Hide ads"], "Greenify": ["Unlock Donation"]})


class FindPkgNameTests(unittest.TestCase):
    class _NamelessScraper:
        def cached_metadata(self, url: str) -> AppMetadata:
            return AppMetadata(pkg_name="", versions=["1.0"])

    class _Entry:
        table = "Sample"
        pkg_name = ""

        def __init__(self, dl_urls: dict[str, str]) -> None:
            self.dl_urls = dl_urls

    def test_source_without_pkg_name_does_not_shadow_others(self) -> None:
        # Regression: the Direct scraper never reports a package name, and it is always
        # tried first, which used to make the whole build run with an empty pkg name.
        entry = self._Entry({"direct": "d", "apkmirror": "a"})
        scrapers = {"direct": self._NamelessScraper(), "apkmirror": FakeScraper(["1.0"])}
        pkg_name, src, failed = builder._find_pkg_name(entry, scrapers)  # type: ignore[arg-type]
        self.assertEqual(pkg_name, "pkg")
        self.assertEqual(src, "apkmirror")
        self.assertEqual(failed, set())

    def test_falls_back_to_nameless_source(self) -> None:
        entry = self._Entry({"direct": "d"})
        pkg_name, src, _ = builder._find_pkg_name(entry, {"direct": self._NamelessScraper()})  # type: ignore[arg-type]
        self.assertEqual(pkg_name, "")
        self.assertEqual(src, "direct")

    def test_config_pkg_name_still_wins(self) -> None:
        entry = self._Entry({"direct": "d", "apkmirror": "a"})
        entry.pkg_name = "com.from.config"
        scrapers = {"direct": self._NamelessScraper(), "apkmirror": FakeScraper(["1.0"])}
        pkg_name, src, _ = builder._find_pkg_name(entry, scrapers)  # type: ignore[arg-type]
        self.assertEqual(pkg_name, "com.from.config")
        self.assertEqual(src, "direct")


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