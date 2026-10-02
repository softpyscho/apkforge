# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
#
# This file is part of apkforge and licensed under the GNU GPLv3.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from src.scripts import readme, wa_version
from src.scripts.logs import combine_logs
from src.scripts.readme import _version_label
from src.scripts.wa_version import _is_version, _patch_version_line, _to_wildcard


class WaVersionTests(unittest.TestCase):
    def test_to_wildcard(self) -> None:
        self.assertEqual(_to_wildcard("2.26.30.85"), "2.26.30.xx")
        self.assertEqual(_to_wildcard("2.26.30"), "2.26.30")
        self.assertEqual(_to_wildcard("latest"), "latest")

    def test_patch_preserves_other_keys(self) -> None:
        content = '[WhatsApp]\nversion = "2.26.30.xx"\nmirror = true\n\n[Other]\nversion = "1.0"\n'
        new, updated = _patch_version_line(content, "WhatsApp", "2.26.31.77")
        self.assertTrue(updated)
        self.assertIn('version = "2.26.31.77"', new)
        self.assertIn("mirror = true", new)
        self.assertIn('[Other]\nversion = "1.0"', new)

    def test_is_version(self) -> None:
        self.assertTrue(_is_version("2.26.37.xx"))
        self.assertTrue(_is_version("2.26.37.10"))
        self.assertFalse(_is_version("latest"))
        self.assertFalse(_is_version(""))

    def test_missing_upstream_versions_keep_config_pinned(self) -> None:
        # Regression: when the upstream array is renamed or empty the fetch falls back
        # to "latest", which used to be written into config.toml and un-pin the mirror
        # from the versions the patcher actually supports.
        content = '[WhatsApp]\nversion = "2.26.37.xx"\nmirror = true\n\n[WhatsApp-Business]\nversion = "2.26.37.xx"\n'
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            cfg.write_text(content, encoding="utf-8")
            os.chdir(tmp)
            try:
                with (
                    mock.patch.object(wa_version, "CONFIG_PATH", cfg),
                    mock.patch.object(wa_version, "fetch_recommended_wa_versions", return_value=("latest", "latest")),
                    mock.patch("sys.stdout", io.StringIO()),
                ):
                    wa_version.update_config_toml()
            finally:
                os.chdir(cwd)
            self.assertEqual(cfg.read_text(encoding="utf-8"), content)

    def test_disabled_table_is_left_untouched(self) -> None:
        # A disabled entry is not built, so rewriting its pin would only produce config
        # churn that CI commits.
        content = '[WhatsApp]\nenabled = false\nversion = "2.26.37.xx"\nmirror = true\n'
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            cfg.write_text(content, encoding="utf-8")
            os.chdir(tmp)
            try:
                with (
                    mock.patch.object(wa_version, "CONFIG_PATH", cfg),
                    mock.patch.object(wa_version, "fetch_recommended_wa_versions", return_value=("2.26.40.xx", "2.26.40.xx")),
                    mock.patch("sys.stdout", io.StringIO()),
                ):
                    wa_version.update_config_toml()
            finally:
                os.chdir(cwd)
            self.assertEqual(cfg.read_text(encoding="utf-8"), content)

    def test_enabled_table_is_still_updated(self) -> None:
        content = '[WhatsApp]\nversion = "2.26.37.xx"\nmirror = true\n'
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "config.toml"
            cfg.write_text(content, encoding="utf-8")
            os.chdir(tmp)
            try:
                with (
                    mock.patch.object(wa_version, "CONFIG_PATH", cfg),
                    mock.patch.object(wa_version, "fetch_recommended_wa_versions", return_value=("2.26.40.xx", "")),
                    mock.patch("sys.stdout", io.StringIO()),
                ):
                    wa_version.update_config_toml()
            finally:
                os.chdir(cwd)
            self.assertIn('version = "2.26.40.xx"', cfg.read_text(encoding="utf-8"))

    def test_is_table_disabled(self) -> None:
        self.assertTrue(wa_version._is_table_disabled('[A]\nenabled = false\n', "A"))
        self.assertFalse(wa_version._is_table_disabled('[A]\nenabled = true\n', "A"))
        self.assertFalse(wa_version._is_table_disabled('[A]\nversion = "1"\n', "A"))
        self.assertFalse(wa_version._is_table_disabled('[B]\nenabled = false\n\n[A]\nversion = "1"\n', "A"))

    def test_patch_missing_table_returns_original(self) -> None:
        content = '[Other]\nversion = "1.0"\n'
        new, updated = _patch_version_line(content, "WhatsApp", "1.0")
        self.assertFalse(updated)
        self.assertEqual(new, content)


_LOGS_CONFIG = """
[Reddit]
app-name = "Reddit"
pkg-name = "com.reddit.frontpage"
arch = "all"
apkmirror-dlurl = "https://www.apkmirror.com/apk/redditinc/reddit"

[Reddit.patches]
"github:MorpheApp/morphe-patches" = []

[Bitget]
app-name = "Bitget"
mirror = true
arch = "arm64-v8a"
pkg-name = "com.bitget.exchange"
uptodown-dlurl = "https://bitget.en.uptodown.com/android"
"""


class CombineLogsTests(unittest.TestCase):
    def _run(self) -> str:
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.toml").write_text(_LOGS_CONFIG, encoding="utf-8")
            (root / "versions_info.json").write_text(json.dumps({
                "success": [
                    {"app": "Reddit", "label": "Reddit", "version": "2026.14.0", "apk": "reddit-morphe-v2026.14.0-all.apk", "source": "apkmirror"},
                    {"app": "Bitget", "label": "Bitget (arm64-v8a)", "version": "2.90.1", "apk": "bitget-mirror-v2.90.1-arm64-v8a.apkm", "source": "uptodown"},
                ],
                "excluded_patches": {"Reddit": ["Hide ads"]},
            }), encoding="utf-8")
            (root / "patches_info.json").write_text(json.dumps({"Reddit": ["Custom branding", "Hide ads"]}), encoding="utf-8")
            (root / "logs").mkdir()
            (root / "logs" / "build-Reddit.md").write_text("- 🟢 » Reddit: `2026.14.0`\n", encoding="utf-8")
            os.chdir(root)
            try:
                with mock.patch("sys.stdout", io.StringIO()) as out:
                    combine_logs("logs")
                    return out.getvalue()
            finally:
                os.chdir(cwd)

    def test_mirror_row_is_not_reported_as_pending(self) -> None:
        # Regression: mirrors have no patch cache, so they used to render as
        # "(Pending cache update)" in every published release note.
        output = self._run()
        self.assertIn("*(None - Stock Mirror)*", output)
        self.assertNotIn("Pending cache update", output)

    def test_arch_all_is_not_reported_as_arm64(self) -> None:
        # Regression: a label without "(arch)" used to be hardcoded to arm64-v8a.
        output = self._run()
        reddit_row = next(line for line in output.splitlines() if "**Reddit**" in line)
        self.assertIn("`all`", reddit_row)
        self.assertNotIn("arm64-v8a", reddit_row)

    def test_excluded_patches_are_marked(self) -> None:
        # Regression: release notes listed excluded patches as if they were applied.
        output = self._run()
        self.assertIn("excluded: build error", output)
        self.assertIn("<s>`Hide ads`</s>", output)


class PatchesLabelMicroGTests(unittest.TestCase):
    class _Entry:
        table = "Xodo"
        app_name = "Xodo"
        exclusive_patches = False

        def __init__(self, microg: bool) -> None:
            self.microg = microg
            self.patcher_args: list[str] = []
            self.patches = {"github:example/patches": {"version": "latest", "include": [], "exclude": []}}

    def test_microg_is_not_listed_as_applied_when_not_opted_in(self) -> None:
        # The builder disables it, so the README must not claim it is applied even when
        # the bundle enables it by default.
        cache = {"Xodo": ["Enable Pro", "GmsCore support (MicroG)"]}
        label = readme._patches_label(self._Entry(microg=False), cache)
        self.assertIn("Enable Pro", label)
        self.assertNotIn("GmsCore", label)
        self.assertIn("1 patch", label)

    def test_microg_is_listed_when_opted_in(self) -> None:
        cache = {"Xodo": ["Enable Pro", "GmsCore support (MicroG)"]}
        label = readme._patches_label(self._Entry(microg=True), cache)
        self.assertIn("GmsCore", label)
        self.assertIn("2 patches", label)

    def test_detection_matches_the_builder(self) -> None:
        for name in ("MicroG integration", "GmsCore support (MicroG)", "gmscore support"):
            self.assertTrue(readme._is_microg_patch(name), name)
        for name in ("Enable Pro", "Hide ads", "Microsoft login"):
            self.assertFalse(readme._is_microg_patch(name), name)


class UpdateReadmeTests(unittest.TestCase):
    def test_only_the_first_marker_pair_is_replaced(self) -> None:
        # Regression: re.sub replaced every marker pair, so documenting the markers in
        # prose made the generated app table be written a second time over that prose.
        cwd = Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "config.toml").write_text(_LOGS_CONFIG, encoding="utf-8")
            (root / "README.md").write_text(
                "# t\n\n<!-- APPS_START -->\nold\n<!-- APPS_END -->\n\nEdit between <!-- APPS_START --> and <!-- APPS_END --> is generated.\n",
                encoding="utf-8",
            )
            os.chdir(root)
            try:
                with mock.patch("sys.stdout", io.StringIO()):
                    readme.update_readme()
                content = (root / "README.md").read_text(encoding="utf-8")
            finally:
                os.chdir(cwd)

        self.assertEqual(content.count("<!-- APPS_START -->"), 2)
        self.assertIn("Edit between <!-- APPS_START --> and <!-- APPS_END --> is generated.", content)
        self.assertNotIn("old", content)


class _Entry:
    def __init__(self, table: str, version: str) -> None:
        self.table = table
        self.version = version
        self.badge_color = "3e9cfb"
        self.patches: dict[str, dict] = {}


class ReadmeVersionLabelTests(unittest.TestCase):
    def test_pinned_version_uses_build_cache(self) -> None:
        label = _version_label(_Entry("WhatsApp", "2.26.30.xx"), {"WhatsApp": "2.26.30.85"})
        self.assertIn("2.26.30.85", label)
        self.assertNotIn("xx", label)

    def test_pinned_version_without_cache_uses_config(self) -> None:
        label = _version_label(_Entry("WhatsApp", "2.26.30.xx"), {})
        self.assertIn("2.26.30.xx", label)


if __name__ == "__main__":
    unittest.main()