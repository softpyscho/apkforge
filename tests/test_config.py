# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
#
# This file is part of apkforge and licensed under the GNU GPLv3.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import unittest

from src.core.config import parse_config


class ParseConfigTests(unittest.TestCase):
    def test_defaults(self) -> None:
        config = parse_config({})
        self.assertGreaterEqual(config.parallel_jobs, 1)
        self.assertEqual(config.brand, "Morphe")
        self.assertEqual(config.cli_version, "latest")

    def test_invalid_parallel_jobs_raises_clear_error(self) -> None:
        with self.assertRaises(ValueError):
            parse_config({"parallel-jobs": "four"})


if __name__ == "__main__":
    unittest.main()
