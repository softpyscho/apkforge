# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
# 
# DO NOT REMOVE OR ALTER THIS COPYRIGHT HEADER.
# This file is part of apkforge.
# Canonical source: https://github.com/softpyscho/apkforge
#
# Licensed under the GNU GPLv3. You may modify this file,
# but you MUST keep this original copyright notice intact
# and prominently state any changes made.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import contextlib
import os
import re
import subprocess
import threading
from pathlib import Path

from src.core.logger import pr, wpr
from src.core.versions import clean_version, highest_version

_SECRET_PATTERNS = re.compile(r"(keystore-password=|keystore-entry-password=)\S+")
_PATCH_TIMEOUT = 900  # 15 minutes


class PatcherError(Exception):
    pass

def _run_java(*args: str | Path, capture: bool = True, timeout: int = 600) -> str:
    result = subprocess.run(["java", *(str(a) for a in args)], capture_output=capture, text=True, timeout=timeout)
    combined = (result.stdout or "") + (result.stderr or "")
    if result.returncode != 0:
        redacted = _SECRET_PATTERNS.sub(r"\1***", combined)
        raise PatcherError(redacted.strip())
    return combined

def _run_java_streaming(args: list[str | Path], timeout: int = _PATCH_TIMEOUT) -> str:
    """Run a java command, streaming merged output live while capturing it."""
    proc = subprocess.Popen([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
    lines: list[str] = []

    def _pump() -> None:
        if proc.stdout is None:
            return
        for line in proc.stdout:
            print(line, end="")
            lines.append(line)
        proc.stdout.close()

    reader = threading.Thread(target=_pump, daemon=True)
    reader.start()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        reader.join(timeout=5)
        captured = _SECRET_PATTERNS.sub(r"\1***", "".join(lines))
        raise PatcherError(f"Command timed out after {timeout}s:\n{captured.strip()}") from None
    else:
        reader.join(timeout=5)

    output = "".join(lines)
    if proc.returncode != 0:
        redacted = _SECRET_PATTERNS.sub(r"\1***", output)
        raise PatcherError(redacted.strip())
    return output

def _parse_patch_block(output: str, patch_name: str) -> list[str]:
    if m := re.search(rf"Name:\s*{re.escape(patch_name)}\n.*?Compatible versions:\s*\n(.*?)(?:\n\n|\Z)", output, re.DOTALL | re.IGNORECASE):
        vers = []
        for line in m.group(1).splitlines():
            clean = clean_version(line)
            if clean:
                vers.append(clean)
        return vers
    return []

def _parse_versions_output(output: str) -> list[str]:
    marker = "Most common compatible versions:\n"
    if marker not in output:
        return []

    block = output.split(marker)[1].split("\n\n")[0]
    versions = []
    for line in block.splitlines():
        if clean_ver := clean_version(line):
            versions.append(clean_ver)
    return versions

_APPLIED_PATCHES = re.compile(r"Applying (\d+) patch", re.IGNORECASE)


def applied_patch_count(cli_output: str) -> int | None:
    """Patches the Morphe CLI actually applied, from its "Applying N patches..." line.

    ``None`` when the line is absent (a different CLI version), so a caller never treats
    an unrecognised output as "zero patches".
    """
    if not (matches := _APPLIED_PATCHES.findall(cli_output)):
        return None
    return int(matches[-1])


def _redact_args(args: list[str | Path]) -> list[str]:
    return [_SECRET_PATTERNS.sub(r"\1***", str(a)) for a in args]

class PatcherCLI:
    def __init__(self, cli_jar: Path, mpp_map: dict[tuple[str, str], Path], ks_path: Path | None = None) -> None:
        self.cli_jar = cli_jar
        self.mpp_map = mpp_map
        self.ks_path = ks_path

    def list_patches(self, pkg_name: str, experimental: bool = False) -> str:
        extra = ["-x"] if experimental else []
        return "".join(_run_java("-jar", self.cli_jar, "list-patches", "--patches", mpp, "-f", pkg_name, "-v", "-p", *extra, timeout=60) for mpp in self.mpp_map.values())

    def list_versions(self, pkg_name: str, experimental: bool = False) -> str:
        extra = ["-x"] if experimental else []
        parts = []
        for mpp in self.mpp_map.values():
            with contextlib.suppress(PatcherError):
                parts.append(_run_java("-jar", self.cli_jar, "list-versions", "--patches", mpp, "-f", pkg_name, *extra, timeout=60))
        return "\n".join(parts)

    def get_last_supported_version(self, list_patches_output: str, pkg_name: str, patches: dict[str, dict], experimental: bool = False) -> str | None:
        all_included = [p for spec in patches.values() for p in spec["include"]]
        all_vers: list[str] = []
        for p in all_included:
            all_vers.extend(_parse_patch_block(list_patches_output, p))
        if all_vers:
            return highest_version(all_vers)

        versions_output = self.list_versions(pkg_name, experimental)
        if "Any" in versions_output:
            return None

        if not (versions := _parse_versions_output(versions_output)):
            raise PatcherError(f"No patches found for '{pkg_name}'")
        return highest_version(versions)

    def resolve_auto_patches(self, list_patches_output: str) -> tuple[str, str]:
        microg_patch = psu_patch = ""
        for line in list_patches_output.splitlines():
            line_lower = line.lower()
            if not line_lower.startswith("name:"):
                continue

            patch_name = line[5:].strip()
            name_lower = patch_name.lower()
            if "gmscore" in name_lower or "microg" in name_lower:
                microg_patch = patch_name
            elif "disable play store updates" in name_lower:
                psu_patch = patch_name
        return microg_patch, psu_patch

    def build_patch_args(self, patches: dict[str, dict], extra_args: list[str], arch: str, auto_patches: tuple[str, str], exclusive: bool = False, force: bool = False, microg: bool = False) -> list[str]:
        microg_patch, psu_patch = auto_patches
        # "Disable Play Store updates" is applied automatically; a GmsCore/MicroG patch
        # is not. Forcing it made the built APK demand MicroG at runtime even for apps
        # that never sign in with Google, so it is opt-in per app via `microg = true`.
        active_auto = {p for p in (psu_patch,) if p}
        if microg and microg_patch:
            active_auto.add(microg_patch)
        # Anything already disabled -- by config or by the retry loop after a patch
        # failed -- must not be re-enabled below, or the exclusion is a no-op and the
        # retry fails on the very same patch.
        disabled = {a for spec in patches.values() for a in spec["exclude"]}
        disabled |= {extra_args[i + 1] for i, a in enumerate(extra_args) if a == "-d" and i + 1 < len(extra_args)}

        p_args: list[str] = ["-f"] if force else []
        for src, spec in patches.items():
            p_args.extend(("--patches", str(self.mpp_map[(src, spec["version"])])))
            for p in spec["include"]:
                if p in active_auto:
                    wpr(f"You can't include '{p}' patch as that's done by builder automatically")
                else:
                    p_args.extend(("-e", p))
            for p in spec["exclude"]:
                p_args.extend(("-d", p))
        if exclusive:
            p_args.append("--exclusive")

        p_args.extend(extra_args)
        for auto_p in active_auto:
            if auto_p in disabled:
                wpr(f"Not re-enabling '{auto_p}' automatically: it is disabled for this build")
                continue
            p_args.extend(("-e", auto_p))

        # Turn the patch off explicitly when the app did not opt in: some bundles ship it
        # enabled by default, which would reintroduce the MicroG dependency silently.
        if microg_patch and not microg:
            requested = {p for spec in patches.values() for p in spec["include"]}
            if microg_patch in requested:
                wpr(f"'{microg_patch}' is listed under [patches].include; set 'microg = true' instead")
            elif microg_patch not in disabled:
                pr(f"Disabling '{microg_patch}': this app does not opt in to MicroG ('microg = true' to enable)")
                p_args.extend(("-d", microg_patch))
        p_args.extend(("--striplibs", "arm64-v8a,armeabi-v7a" if arch == "all" else arch))
        return p_args

    def patch(self, stock_apk: Path, output_apk: Path, patch_args: list[str]) -> str:
        base_cmd = ["-jar", self.cli_jar, "patch", stock_apk, "-o", output_apk]
        ks_args: list[str] = []
        if self.ks_path and (ks_pass := os.getenv("KEYSTORE_PASS")) and (ks_alias := os.getenv("KEYSTORE_ALIAS")):
            ks_args = [f"--keystore={self.ks_path}", f"--keystore-entry-password={ks_pass}", f"--keystore-password={ks_pass}", f"--signer={ks_alias}", f"--keystore-entry-alias={ks_alias}"]
        elif Path("morphe.keystore").exists():
            ks_args = ["--keystore=morphe.keystore"]

        pr(" ".join(_redact_args(["java", *base_cmd, *ks_args, *patch_args])))
        try:
            return _run_java_streaming(["java", *base_cmd, *ks_args, *patch_args])
        except PatcherError:
            output_apk.unlink(missing_ok=True)
            raise