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

    def test_hyphenated_versions_are_not_truncated(self) -> None:
        # Regression: the version was cut at the first hyphen, so a cached
        # "12.19.1-release.0" was reported as "12.19.1" and the cached-APK fallback
        # then asked its source for a version that never existed.
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            for name in (
                "com.example-v12.19.1-release.0-arm64-v8a.apk",
                "com.example-v2.3.2-android-armeabi-v7a.apk",
                "com.example-v6.71.15-API29-x86_64.apk",
                "com.example-v2026.09.29-d4b0f3fe-all.apkm",
                "com.example-v1.2.3-x86.apk",
            ):
                (cache / name).write_bytes(b"x")
            with mock.patch.object(builder, "ORIGINAL_APK_DIR", cache):
                versions = sorted(builder._cached_apk_versions("com.example"))

        self.assertEqual(versions, [
            "1.2.3",
            "12.19.1-release.0",
            "2.3.2-android",
            "2026.09.29-d4b0f3fe",
            "6.71.15-API29",
        ])

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


def _apk_bytes() -> bytes:
    return b"PK\x03\x04" + b"0" * 200_000


class WildcardFallbackDownloadTests(unittest.TestCase):
    """The WhatsApp failure: the one reachable source served a different minor."""

    class _Entry:
        table = "WhatsApp"
        dpi = ""

        def __init__(self) -> None:
            self.version = "2.26.37.xx"
            self.dl_urls = {"direct": "d", "apkmirror": "a", "uptodown": "u"}

    class _DirectScraper:
        """Serves a real APK, but of a minor the wildcard does not cover."""

        def __init__(self) -> None:
            self.calls = 0

        def download(self, url, version, dest, arch, dpi):
            self.calls += 1
            dest.write_bytes(_apk_bytes())
            return builder.DownloadResult(path=dest, is_bundle=False, original_name="WhatsApp.apk")

    class _BlockedScraper:
        def download(self, url, version, dest, arch, dpi):
            raise builder.NetworkError("Bot challenge")

    def _run(self, verify: bool):
        direct = self._DirectScraper()
        scrapers = {"direct": direct, "apkmirror": self._BlockedScraper(), "uptodown": self._BlockedScraper()}
        # The downloaded artifact is a 2.26.38.x build, so it never matches the pin.
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp)),
            mock.patch.object(builder, "_matches_wildcard", return_value=False),
        ):
            result = builder._download_apk(
                self._Entry(), "2.26.37.74", "arm64-v8a", "com.whatsapp", scrapers,  # type: ignore[arg-type]
                "direct", {"apkmirror", "uptodown"}, verify_wildcard=verify,
            )
            return result, direct.calls, result.path.exists()

    def test_mismatched_artifact_is_accepted_once_no_source_can_match(self) -> None:
        # Regression: the artifact was discarded and the build failed with "Stock APK
        # not found", so the mirror could never be published again.
        result, calls, exists = self._run(verify=True)
        self.assertEqual(result.source_used, "direct")
        self.assertTrue(exists)
        self.assertEqual(calls, 2, "expected a strict pass and then a relaxed retry")

    def test_strict_pass_is_still_preferred(self) -> None:
        # With verification off the very first attempt is accepted: the relaxed retry
        # must not become the normal path.
        _, calls, _ = self._run(verify=False)
        self.assertEqual(calls, 1)

    def test_all_sources_failing_still_raises(self) -> None:
        scrapers = {"direct": self._BlockedScraper(), "apkmirror": self._BlockedScraper()}
        entry = self._Entry()
        entry.dl_urls = {"direct": "d", "apkmirror": "a"}
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp)),
            self.assertRaises(builder.BuilderError),
        ):
            builder._download_apk(entry, "2.26.37.74", "arm64-v8a", "com.whatsapp", scrapers, "direct", set(), verify_wildcard=True)  # type: ignore[arg-type]


class ResolveVersionFallbackTests(unittest.TestCase):
    def test_wildcard_with_no_candidates_does_not_fabricate_a_version(self) -> None:
        # Regression: this produced "2.26.37.0" -- a version nobody ever published --
        # and every source was then asked for it.
        class _Dead:
            def cached_metadata(self, url):
                raise builder.NetworkError("410 Gone")

        entry = FakeEntry(version="2.26.37.xx", dl_urls={"apkmirror": "a", "uptodown": "u"})
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp)):
            version, _ = builder._resolve_version(entry, None, "", "com.whatsapp.w4b", "apkmirror", {"apkmirror": _Dead(), "uptodown": _Dead()})  # type: ignore[arg-type]
        self.assertEqual(version, "latest")


class CachedDownloadValidationTests(unittest.TestCase):
    def test_truncated_cached_apk_is_discarded_and_redownloaded(self) -> None:
        class _Scraper:
            def download(self, url, version, dest, arch, dpi):
                dest.write_bytes(_apk_bytes())
                return builder.DownloadResult(path=dest, is_bundle=False)

        entry = WildcardFallbackDownloadTests._Entry()
        entry.version = "1.2.3"
        entry.dl_urls = {"direct": "d"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp)):
            stale = Path(tmp) / "com.example-v1.2.3-arm64-v8a.apk"
            stale.write_bytes(b"<html>error</html>")
            result = builder._download_apk(entry, "1.2.3", "arm64-v8a", "com.example", {"direct": _Scraper()}, "direct", set())  # type: ignore[arg-type]
            self.assertEqual(result.source_used, "direct")
            self.assertGreater(result.path.stat().st_size, 100_000)


class ZeroPatchGuardTests(unittest.TestCase):
    """Replays the Greenify job: patch fails on an unsupported APK, is excluded, and the
    CLI then "applies 0 patches" and exits 0.
    """

    class _Entry:
        table = "Greenify"
        app_name = "Greenify"
        brand = "morphe"
        exclusive_patches = False
        microg = False
        patcher_args: list[str] = []  # noqa: RUF012
        patches = {"github:r/p": {"version": "latest", "include": [], "exclude": []}}  # noqa: RUF012

    class _Patcher:
        def __init__(self, outputs: list[str | Exception]) -> None:
            self.outputs, self.calls = list(outputs), 0

        def resolve_auto_patches(self, _):
            return ("", "")

        def build_patch_args(self, **kwargs):
            return list(kwargs["extra_args"])

        def patch(self, stock, out, args):
            self.calls += 1
            result = self.outputs.pop(0)
            if isinstance(result, Exception):
                raise result
            out.write_bytes(b"PK\x03\x04" + b"0" * 1000)
            return result

    def _run(self, outputs):
        patcher = self._Patcher(outputs)
        dl = builder.DownloadResult(path=Path("stock.apk"), is_bundle=False)
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "build").mkdir()
            with (
                mock.patch.object(builder, "TEMP_DIR", tmp_path),
                mock.patch.object(builder, "BUILD_DIR", tmp_path / "build"),
            ):
                result = builder._patch_with_retries(self._Entry(), "arm64-v8a", "4.7.5", True, patcher, "", dl)  # type: ignore[arg-type]
        return result, patcher

    def test_every_patch_excluded_is_a_failure_not_a_patched_build(self) -> None:
        failure = builder.PatcherError("SEVERE: FAILED: Unlock Donation\nPatchException: Failed to match the fingerprint")
        (apk, excluded, exc), patcher = self._run([failure, "INFO: Applying 0 patches... \nINFO: Saved to out.apk"])
        self.assertIsNone(apk, "a build with zero patches must not be reported as built")
        self.assertEqual(excluded, ["Unlock Donation"])
        self.assertIn("No patches could be applied", str(exc))
        self.assertIn("Unlock Donation", str(exc))
        self.assertEqual(patcher.calls, 2)

    def test_a_normal_build_is_unaffected(self) -> None:
        (apk, excluded, exc), _ = self._run(["INFO: Applying 3 patches... "])
        self.assertIsNotNone(apk)
        self.assertEqual(excluded, [])
        self.assertIsNone(exc)

    def test_one_excluded_patch_with_others_remaining_still_ships(self) -> None:
        failure = builder.PatcherError("SEVERE: FAILED: Bad Patch")
        (apk, excluded, _), _ = self._run([failure, "INFO: Applying 4 patches... "])
        self.assertIsNotNone(apk)
        self.assertEqual(excluded, ["Bad Patch"])

    def test_an_unrecognised_cli_output_is_not_mistaken_for_zero(self) -> None:
        (apk, _, exc), _ = self._run(["no recognisable line here"])
        self.assertIsNotNone(apk)
        self.assertIsNone(exc)


class BlockedSourceTests(unittest.TestCase):
    """Uptodown has the version but gates the download; retrying other versions is futile."""

    class _Entry:
        table = "Bitget"
        dpi = ""
        version = "latest"

        def __init__(self) -> None:
            self.dl_urls = {"apkmirror": "a", "uptodown": "u"}

    class _Gated:
        def __init__(self, versions) -> None:
            self.versions, self.asked = versions, []

        def cached_metadata(self, url):
            return AppMetadata(pkg_name="pkg", versions=list(self.versions))

        def known_versions(self, url):
            return list(self.versions)

        def download(self, url, version, dest, arch, dpi):
            self.asked.append(version)
            raise builder.SourceBlockedError("Download is gated behind a Cloudflare Turnstile challenge")

    def test_a_blocked_source_is_asked_once_not_once_per_candidate(self) -> None:
        # Regression: the Bitget job made four futile attempts, one per lower version.
        gated = self._Gated(["2.92.1", "2.92.3", "2.93.2", "2.94.0", "2.94.2"])
        entry = self._Entry()
        entry.dl_urls = {"uptodown": "u"}
        failed: set[str] = set()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp)):
            with self.assertRaises(builder.BuilderError):
                builder._download_apk(entry, "2.94.2", "arm64-v8a", "pkg", {"uptodown": gated}, "uptodown", failed)  # type: ignore[arg-type]
            result, version = builder._online_fallback(entry, "2.94.2", "arm64-v8a", "pkg", {"uptodown": gated}, failed)  # type: ignore[arg-type]
        self.assertIsNone(result)
        self.assertEqual(version, "")
        self.assertEqual(gated.asked, ["2.94.2"], "the gated source must be asked exactly once")
        self.assertIn("uptodown", failed)

    def test_a_plain_miss_does_not_block_the_source(self) -> None:
        class _Missing(self._Gated):
            def download(self, url, version, dest, arch, dpi):
                self.asked.append(version)
                raise builder.ScraperError("Version not found")

        src = _Missing(["1.0.0"])
        entry = self._Entry()
        entry.dl_urls = {"apkmirror": "a"}
        failed: set[str] = set()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp)), self.assertRaises(builder.BuilderError):
            builder._download_apk(entry, "2.0.0", "arm64-v8a", "pkg", {"apkmirror": src}, "apkmirror", failed)  # type: ignore[arg-type]
        self.assertNotIn("apkmirror", failed, "a missing version says nothing about the other versions")

    def test_fallback_never_retries_the_target_version(self) -> None:
        class _Missing(self._Gated):
            def download(self, url, version, dest, arch, dpi):
                self.asked.append(version)
                raise builder.ScraperError("Version not found")

        src = _Missing(["2.93.0", "2.94.2"])
        entry = self._Entry()
        entry.dl_urls = {"apkmirror": "a"}
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp)):
            builder._online_fallback(entry, "2.94.2", "arm64-v8a", "pkg", {"apkmirror": src}, set())  # type: ignore[arg-type]
        self.assertEqual(src.asked, ["2.93.0"], "the target was already tried; only the lower candidate remains")

    def test_hint_reports_what_the_source_lists(self) -> None:
        hint = builder._offers_hint(self._Gated(["5.1.1", "4.7.5"]), "u")  # type: ignore[arg-type]
        self.assertIn("2 version(s)", hint)
        self.assertIn("'5.1.1'", hint)

    def test_hint_never_raises(self) -> None:
        self.assertEqual(builder._offers_hint(object(), "u"), "")  # type: ignore[arg-type]


class OnlineFallbackTests(unittest.TestCase):
    """Regression: a stale cache was preferred over a version a source actually has.

    Greenify resolved 5.1.1 (the newest its patches support), APKMirror did not carry
    that exact version and Uptodown's download was Turnstile-blocked, so the build fell
    straight back to a cached 4.7.5 — shipping an outdated app and making the
    "Unlock Donation" patch, written against 5.x, fail.
    """

    class _Entry:
        table = "Greenify"
        dpi = ""
        mirror = False
        app_name = "Greenify"

        def __init__(self) -> None:
            self.version = "auto"
            self.dl_urls = {"apkmirror": "a", "uptodown": "u"}

    class _Source:
        """Offers `versions`, but only serves the ones in `downloadable`."""

        def __init__(self, versions, downloadable) -> None:
            self.versions, self.downloadable = versions, set(downloadable)
            self.asked: list[str] = []

        def cached_metadata(self, url):
            return AppMetadata(pkg_name="com.oasisfeng.greenify", versions=list(self.versions))

        def download(self, url, version, dest, arch, dpi):
            self.asked.append(version)
            if version not in self.downloadable:
                raise builder.ScraperError("Version not found")
            dest.write_bytes(b"PK\x03\x04" + b"0" * 200_000)
            return builder.DownloadResult(path=dest, is_bundle=False)

    def _run(self, cached: list[str]):
        apkmirror = self._Source(["4.7.5", "5.0.9", "5.1.0", "5.1.1"], downloadable={"5.1.0", "5.0.9", "4.7.5"})
        uptodown = self._Source(["5.1.1"], downloadable=set())
        scrapers = {"apkmirror": apkmirror, "uptodown": uptodown}
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            for v in cached:
                (cache / f"com.oasisfeng.greenify-v{v}-arm64-v8a.apk").write_bytes(b"PK\x03\x04" + b"0" * 200_000)
            with mock.patch.object(builder, "ORIGINAL_APK_DIR", cache):
                result, version = builder._online_fallback(
                    self._Entry(), "5.1.1", "arm64-v8a", "com.oasisfeng.greenify", scrapers, set(),  # type: ignore[arg-type]
                )
                return result, version, apkmirror.asked

    def test_newest_downloadable_version_wins_over_a_stale_cache(self) -> None:
        result, version, asked = self._run(cached=["4.7.5"])
        self.assertIsNotNone(result)
        self.assertEqual(version, "5.1.0", "should take the newest version the source can actually serve")
        self.assertEqual(asked[0], "5.1.0", "candidates must be tried highest-first")
        self.assertNotIn("4.7.5", asked[:1])

    def test_reachable_sources_are_tried_before_challenged_ones(self) -> None:
        # SOURCES order puts apkmirror ahead of uptodown, so the Turnstile-blocked
        # source is not burned through first.
        _, _, asked = self._run(cached=[])
        self.assertTrue(asked, "apkmirror should have been asked")

    def test_candidates_are_capped(self) -> None:
        src = self._Source([f"1.0.{n}" for n in range(40)], downloadable=set())
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp)):
            entry = self._Entry()
            entry.dl_urls = {"apkmirror": "a"}
            result, _ = builder._online_fallback(entry, "9.9.9", "arm64-v8a", "pkg", {"apkmirror": src}, set())  # type: ignore[arg-type]
        self.assertIsNone(result)
        # five capped candidates plus the single "highest available" retry
        self.assertLessEqual(len(src.asked), builder._MAX_FALLBACK_CANDIDATES + 1)

    def test_no_usable_source_returns_nothing_so_the_cache_can_be_used(self) -> None:
        dead = self._Source([], downloadable=set())
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp)):
            entry = self._Entry()
            entry.dl_urls = {"apkmirror": "a"}
            result, version = builder._online_fallback(entry, "5.1.1", "arm64-v8a", "pkg", {"apkmirror": dead}, set())  # type: ignore[arg-type]
        self.assertIsNone(result)
        self.assertEqual(version, "")


class RenameCachedStockTests(unittest.TestCase):
    def _cache(self, tmp: Path, name: str) -> builder.DownloadResult:
        (tmp / name).write_bytes(b"PK\x03\x04")
        (tmp / name).with_suffix(".src").write_text("github", encoding="utf-8")
        (tmp / name).with_suffix(".orig").write_text("Duck.Detector.apk", encoding="utf-8")
        return builder.DownloadResult(path=tmp / name, is_bundle=False, original_name="Duck.Detector.apk", source_used="github")

    def test_placeholder_named_cache_is_rekeyed_and_survives_cleanup(self) -> None:
        # Regression: the download is named after the requested version, so a placeholder
        # such as "nightly" made _cleanup_outdated_apks delete the APK it just fetched
        # and made _cached_apk_versions report "nightly" as an available version -- which
        # outranks every real version in highest_version().
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            result = self._cache(cache, "Duck.Detector-vnightly-all.apk")
            with mock.patch.object(builder, "ORIGINAL_APK_DIR", cache):
                renamed = builder._rename_cached_stock(result, "Duck.Detector", "all", "2026.09.29-d4b0f3fe")
                builder._cleanup_outdated_apks("Duck.Detector", keep_version="2026.09.29-d4b0f3fe")
                versions = builder._cached_apk_versions("Duck.Detector")
                names = sorted(f.name for f in cache.iterdir())
                survived = renamed.path.exists()

        self.assertEqual(renamed.path.name, "Duck.Detector-v2026.09.29-d4b0f3fe-all.apk")
        self.assertTrue(survived)
        self.assertEqual(renamed.original_name, "Duck.Detector.apk")
        self.assertEqual(renamed.source_used, "github")
        self.assertEqual(versions, ["2026.09.29-d4b0f3fe"])
        self.assertEqual(names, [
            "Duck.Detector-v2026.09.29-d4b0f3fe-all.apk",
            "Duck.Detector-v2026.09.29-d4b0f3fe-all.orig",
            "Duck.Detector-v2026.09.29-d4b0f3fe-all.src",
        ])

    def test_matching_name_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            result = self._cache(cache, "com.example-v1.2.3-arm64-v8a.apk")
            with mock.patch.object(builder, "ORIGINAL_APK_DIR", cache):
                self.assertIs(builder._rename_cached_stock(result, "com.example", "arm64-v8a", "1.2.3"), result)

    def test_artifact_outside_the_cache_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            outside = Path(tmp) / "elsewhere"
            outside.mkdir()
            result = self._cache(outside, "com.example-vnightly-all.apk")
            with mock.patch.object(builder, "ORIGINAL_APK_DIR", Path(tmp) / "cache"):
                self.assertIs(builder._rename_cached_stock(result, "com.example", "all", "1.2.3"), result)

    def test_empty_pkg_name_is_left_alone(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp)
            result = self._cache(cache, "-vnightly-all.apk")
            with mock.patch.object(builder, "ORIGINAL_APK_DIR", cache):
                self.assertIs(builder._rename_cached_stock(result, "", "all", "1.2.3"), result)


class VersionFilenamePartTests(unittest.TestCase):
    def test_strips_metadata_and_v_prefix(self) -> None:
        self.assertEqual(builder._version_filename_part("v1.2.3"), "1.2.3")
        self.assertEqual(builder._version_filename_part("1.2.3 [versionCode: 9]"), "1.2.3")
        self.assertEqual(builder._version_filename_part("2.3.2-android"), "2.3.2-android")


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