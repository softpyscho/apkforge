# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
#
# This file is part of apkforge and licensed under the GNU GPLv3.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import unittest

from src.scripts.readme import _version_label
from src.scripts.wa_version import _patch_version_line, _to_wildcard


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

    def test_patch_missing_table_returns_original(self) -> None:
        content = '[Other]\nversion = "1.0"\n'
        new, updated = _patch_version_line(content, "WhatsApp", "1.0")
        self.assertFalse(updated)
        self.assertEqual(new, content)


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