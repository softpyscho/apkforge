# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
#
# This file is part of apkforge and licensed under the GNU GPLv3.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import unittest
from pathlib import Path

from src.core.patcher import PatcherCLI

# What hoo-dles/patches-1.45.0.mpp reports for com.xodo.pdf.reader: a MicroG patch that
# is NOT enabled by default.
_LIST_NON_DEFAULT_MICROG = """Name: Enable Pro
Default: true
Name: MicroG integration
Default: false
Name: Disable Play Store updates
Default: false
"""

# What rushiranpise/morphe-patches reports: the GmsCore patch IS a default.
_LIST_DEFAULT_GMSCORE = """Name: Unlock Pro
Default: true
Name: GmsCore support (MicroG)
Default: true
"""

_SRC = "github:example/patches"
_SPEC: dict[str, dict] = {_SRC: {"version": "latest", "include": [], "exclude": []}}


def _cli() -> PatcherCLI:
    return PatcherCLI(Path("cli.jar"), {(_SRC, "latest"): Path("patches.mpp")})


def _pairs(args: list[str], flag: str) -> list[str]:
    return [args[i + 1] for i, a in enumerate(args) if a == flag and i + 1 < len(args)]


class ResolveAutoPatchesTests(unittest.TestCase):
    def test_detects_microg_and_play_store_patches(self) -> None:
        self.assertEqual(_cli().resolve_auto_patches(_LIST_NON_DEFAULT_MICROG),
                         ("MicroG integration", "Disable Play Store updates"))

    def test_detects_gmscore_naming(self) -> None:
        microg, _ = _cli().resolve_auto_patches(_LIST_DEFAULT_GMSCORE)
        self.assertEqual(microg, "GmsCore support (MicroG)")


class MicroGOptInTests(unittest.TestCase):
    """Regression: the builder force-enabled any MicroG/GmsCore patch for every app.

    Xodo and Amazon Prime Video were built with `-e MicroG integration` even though
    neither config asked for it, so the APKs demanded MicroG at runtime.
    """

    def _args(self, list_output: str, microg: bool, **kwargs) -> list[str]:
        cli = _cli()
        spec = kwargs.pop("patches", _SPEC)
        return cli.build_patch_args(
            patches=spec, extra_args=kwargs.pop("extra_args", []), arch="arm64-v8a",
            auto_patches=cli.resolve_auto_patches(list_output), microg=microg, **kwargs,
        )

    def test_microg_is_not_enabled_by_default(self) -> None:
        args = self._args(_LIST_NON_DEFAULT_MICROG, microg=False)
        self.assertNotIn("MicroG integration", _pairs(args, "-e"))

    def test_microg_is_explicitly_disabled_so_bundle_defaults_cannot_apply_it(self) -> None:
        args = self._args(_LIST_DEFAULT_GMSCORE, microg=False)
        self.assertIn("GmsCore support (MicroG)", _pairs(args, "-d"))
        self.assertNotIn("GmsCore support (MicroG)", _pairs(args, "-e"))

    def test_opting_in_enables_it(self) -> None:
        args = self._args(_LIST_NON_DEFAULT_MICROG, microg=True)
        self.assertIn("MicroG integration", _pairs(args, "-e"))
        self.assertNotIn("MicroG integration", _pairs(args, "-d"))

    def test_play_store_patch_is_still_automatic(self) -> None:
        for microg in (False, True):
            args = self._args(_LIST_NON_DEFAULT_MICROG, microg=microg)
            self.assertIn("Disable Play Store updates", _pairs(args, "-e"), f"microg={microg}")

    def test_no_microg_patch_in_bundle_adds_no_flags(self) -> None:
        args = self._args("Name: Enable Pro\nDefault: true\n", microg=False)
        self.assertEqual(_pairs(args, "-d"), [])

    def test_an_explicit_include_is_not_contradicted(self) -> None:
        # Listing it under [patches].include while microg is off must not emit -e and -d
        # for the same patch; the include wins and a warning points at the right key.
        spec = {_SRC: {"version": "latest", "include": ["MicroG integration"], "exclude": []}}
        args = self._args(_LIST_NON_DEFAULT_MICROG, microg=False, patches=spec)
        self.assertIn("MicroG integration", _pairs(args, "-e"))
        self.assertNotIn("MicroG integration", _pairs(args, "-d"))

    def test_a_failed_patch_exclusion_is_not_contradicted(self) -> None:
        args = self._args(_LIST_DEFAULT_GMSCORE, microg=True, extra_args=["-d", "GmsCore support (MicroG)"])
        self.assertNotIn("GmsCore support (MicroG)", _pairs(args, "-e"))

    def test_striplibs_is_always_last(self) -> None:
        args = self._args(_LIST_DEFAULT_GMSCORE, microg=False)
        self.assertEqual(args[-2:], ["--striplibs", "arm64-v8a"])


if __name__ == "__main__":
    unittest.main()
