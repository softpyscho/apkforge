# ---------------------------------------------------------
# Copyright (C) 2026 softpsycho
#
# DO NOT REMOVE OR ALTER THIS COPYRIGHT HEADER.
# This file is part of apkforge.
# Canonical source: https://github.com/softpsycho/apkforge
#
# Licensed under the GNU GPLv3. You may modify this file,
# but you MUST keep this original copyright notice intact
# and prominently state any changes made.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

"""Merge per-build ``patches_info-*.json`` files into a single canonical file.

In the CI build matrix, each parallel job uploads its own
``patches_info-<label>.json`` to the draft release. The release job
downloads all of them, merges them into a single ``patches_info.json``,
and removes the per-job files. Previously this was done by an inline
Python one-liner in ``.github/workflows/build.yml`` — fragile and
untested. This script is the testable replacement.

Usage:
    uv run python -m src.scripts.merge_patches_info [--root DIR] [--output PATH]

Reads every ``patches_info-*.json`` file under ``--root`` (default: ``patches/``)
plus an existing ``patches_info.json`` in the current directory if present,
merges them (later files override earlier ones for the same app key),
and writes the result to ``--output`` (default: ``patches_info.json``).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def merge_patches_info(root: Path, existing: Path | None = None) -> dict[str, list[str]]:
    """Merge all ``patches_info-*.json`` files under ``root``.

    Args:
        root: Directory containing ``patches_info-*.json`` files.
        existing: Optional path to an existing ``patches_info.json`` to
            use as the initial state (entries are preserved unless
            overridden by a per-job file).

    Returns:
        A dict mapping ``app_table_name -> [patch_name, ...]``.
    """
    merged: dict[str, list[str]] = {}
    if existing and existing.exists():
        try:
            data = json.loads(existing.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged = {str(k): [str(p) for p in v] for k, v in data.items() if isinstance(v, list)}
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: could not parse {existing}: {exc}", file=sys.stderr)

    if not root.is_dir():
        return merged

    # Sort for deterministic merge order.
    for f in sorted(root.glob("patches_info-*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"Warning: could not parse {f}: {exc}", file=sys.stderr)
            continue
        if not isinstance(data, dict):
            continue
        for k, v in data.items():
            if isinstance(v, list):
                merged[str(k)] = [str(p) for p in v]

    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("patches"), help="Directory containing patches_info-*.json files (default: patches/)")
    parser.add_argument("--output", type=Path, default=Path("patches_info.json"), help="Output path (default: patches_info.json)")
    args = parser.parse_args()

    existing = args.output if args.output.exists() else None
    merged = merge_patches_info(args.root, existing=existing)
    args.output.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(f"Wrote {args.output} ({len(merged)} apps)")


if __name__ == "__main__":
    main()
