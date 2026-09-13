# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
#
# This file is part of apkforge and licensed under the GNU GPLv3.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import unittest

from src.core.versions import (
    clean_version,
    highest_tag,
    highest_version,
    parse_version,
    version_sort_key,
)


class VersionTests(unittest.TestCase):
    def test_clean_version(self) -> None:
        self.assertEqual(clean_version("1.2.3 (456)"), "1.2.3")
        self.assertEqual(clean_version("1.2.3 [versionCodes: 1]"), "1.2.3")
        self.assertEqual(clean_version(""), "")

    def test_parse_version_ordering(self) -> None:
        self.assertLess(parse_version("1.2.9"), parse_version("1.10.0"))
        self.assertLess(parse_version("1.2.3 (4)"), parse_version("1.2.4"))
        self.assertLess(parse_version("1.2"), parse_version("1.2.1"))
        self.assertGreater(parse_version("1.2.1-release.0"), parse_version("1.2.1"))

    def test_highest_version(self) -> None:
        self.assertEqual(highest_version(["2.26.30.85", "2.26.34.77", "2.26.30.70"]), "2.26.34.77")

    def test_highest_version_empty_raises(self) -> None:
        with self.assertRaises(ValueError):
            highest_version([])

    def test_version_sort_key(self) -> None:
        self.assertEqual(version_sort_key("v1.10.2"), (1, 10, 2))
        self.assertEqual(version_sort_key("nonsense"), (0,))

    def test_highest_tag(self) -> None:
        self.assertEqual(highest_tag(["v1.9.0", "v1.10.0", "v1.2.3"]), "v1.10.0")


if __name__ == "__main__":
    unittest.main()