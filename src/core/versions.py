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

"""Shared version parsing helpers used across the builder, patcher and scripts."""

import re


def clean_version(ver: str) -> str:
    """Strip bracketed metadata (e.g. ``[versionCodes: ...]``) or parenthesized annotations."""
    if not ver:
        return ""
    return re.split(r"[\(\[]", ver.strip())[0].strip()


def parse_version(ver: str) -> tuple:
    """Parse an APK version string into a comparable tuple key.

    Numeric components sort before non-numeric suffix tokens, so a
    suffixed build such as ``1.2.1-release.0`` ranks above ``1.2.1``.
    """
    cleaned = re.sub(r"^[vV]", "", clean_version(ver).strip())
    parts = []
    for token in re.split(r"[._-]", cleaned):
        if token.isdigit():
            parts.append((0, int(token), ""))
        else:
            m = re.match(r"^(\d+)(.*)$", token)
            if m:
                parts.append((0, int(m.group(1)), m.group(2)))
            else:
                parts.append((1, 0, token))
    return tuple(parts)


def version_sort_key(ver: str) -> tuple[int, ...]:
    """Numeric-only sort key for release tags with mixed suffixes."""
    base = clean_version(ver).split("-")[0]
    return tuple(int(x) for x in re.findall(r"\d+", base)) or (0,)


def highest_version(versions: list[str]) -> str:
    """Return the highest APK version using :func:`parse_version` semantics."""
    clean = [v.strip() for v in versions if v.strip()]
    if not clean:
        raise ValueError("Empty version list")
    return max(clean, key=parse_version)


def highest_tag(tags: list[str]) -> str:
    """Return the highest release tag using numeric-only semantics."""
    clean = [t.strip() for t in tags if t.strip()]
    if not clean:
        raise ValueError("Empty tag list")
    return max(clean, key=version_sort_key)