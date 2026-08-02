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

"""apkforge CLI entrypoint.

Usage:
    uv run main.py [build] [target] [arch]    # build (default subcommand)
    uv run main.py list [--filter NAME]        # list configured apps
    uv run main.py clear                       # remove build/ and temp/
    uv run main.py --version                   # print version and exit
    uv run main.py --help                      # this message

Backwards-compatible: bare positional args are still accepted, so
existing CI invocations (`uv run main.py Reddit arm64-v8a`) keep working.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import signal
import subprocess
import sys
import threading
from dataclasses import replace as _dc_replace
from pathlib import Path

# ``copy.replace`` is Python 3.13+; fall back to dataclasses.replace on older versions.
try:
    from copy import replace as _copy_replace
    def replace(obj, **changes):
        return _copy_replace(obj, **changes)
except ImportError:  # pragma: no cover - Python <3.13
    def replace(obj, **changes):
        return _dc_replace(obj, **changes)

try:
    from importlib.metadata import PackageNotFoundError, version as _pkg_version
except ImportError:  # pragma: no cover - Python <3.8 fallback
    _pkg_version = None  # type: ignore[assignment]
    PackageNotFoundError = Exception  # type: ignore[misc, assignment]

from src.core.builder import run_build
from src.core.config import BUILD_DIR, CONFIG_PATH, TEMP_DIR, VALID_ARCHES, AppEntry, load_toml, parse_app_entries, parse_config
from src.core.logger import abort, epr, mark_interrupted, pr, set_verbose, wpr
from src.core.network import NetworkManager

_shutting_down = False
_shutdown_event = threading.Event()


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Load a ``.env`` file into ``os.environ`` if present.

    Uses ``python-dotenv`` if installed; otherwise falls back to a small
    hand-rolled parser that handles ``KEY=value``, ``KEY="value"``,
    comments (``#``) and blank lines.
    """
    if not path.is_file():
        return
    try:
        from dotenv import load_dotenv  # type: ignore[import-not-found]
        load_dotenv(path)
        return
    except ImportError:
        pass

    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        # Strip inline comments after the value (only when value is unquoted).
        value = value.strip()
        if value and value[0] in "\"'" and value[-1] == value[0]:
            value = value[1:-1]
        else:
            value = value.split(" #", 1)[0].rstrip()
        if key and key not in os.environ:
            os.environ[key] = value


def _require_java(min_version: int = 21) -> None:
    if not shutil.which("java"):
        abort(f"Java not found. Please install Java {min_version} or higher")

    result = subprocess.run(["java", "-version"], capture_output=True, text=True)
    match = re.search(r'version "(\d+)', result.stderr)
    if not match:
        abort("Could not determine Java version")

    version = int(match.group(1))
    if version < min_version:
        abort(f"Java {version} found, but Java {min_version}+ is required")


def _get_version() -> str:
    if _pkg_version is None:
        return "0.0.0+unknown"
    try:
        return _pkg_version("apkforge")
    except PackageNotFoundError:
        # Not installed as a package — fall back to pyproject.toml version.
        try:
            import tomllib
            with open(Path(__file__).parent / "pyproject.toml", "rb") as fp:
                data = tomllib.load(fp)
            return str(data.get("project", {}).get("version", "0.0.0"))
        except Exception:
            return "0.0.0+unknown"


def _build(target_app: str | None = None, arch_override: str | None = None, config: object = None) -> int:
    _require_java()
    data = load_toml(CONFIG_PATH)
    main_cfg = parse_config(data)
    pr(f"Loaded config '{CONFIG_PATH}'")
    entries: list[AppEntry] = [e for e in parse_app_entries(data, main_cfg) if e.enabled and (not target_app or e.table == target_app)]
    if target_app and not entries:
        abort(f"App '{target_app}' not found in config")

    if arch_override:
        entries = [replace(e, arch=arch_override) for e in entries]

    TEMP_DIR.mkdir(parents=True, exist_ok=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    for cl in TEMP_DIR.glob("*/changelog.md"):
        cl.write_text("", encoding="utf-8")

    Path("build.md").write_text("", encoding="utf-8")
    with NetworkManager(allow_insecure=main_cfg.allow_insecure) as net:
        success = run_build(entries, main_cfg, net)
    return 0 if success else 1


def _list_apps(filter_name: str | None = None) -> int:
    """Print the resolved app entries without building anything (``--dry-run``).

    Useful for sanity-checking config edits and for CI to verify which apps
    would be built before committing to a full release run.
    """
    data = load_toml(CONFIG_PATH)
    main_cfg = parse_config(data)
    entries = parse_app_entries(data, main_cfg)
    if filter_name:
        entries = [e for e in entries if filter_name.lower() in e.table.lower()]
    if not entries:
        epr("No apps match the filter")
        return 1

    pr(f"{'App':<24} {'Arch':<14} {'Version':<10} {'Release-Group':<14} Sources")
    pr("-" * 100)
    for e in entries:
        if not e.enabled:
            continue
        sources = ", ".join(e.patches.keys()) or "-"
        arches = ("arm64-v8a", "armeabi-v7a") if e.arch == "both" else (e.arch,)
        pr(f"{e.table:<24} {','.join(arches):<14} {e.version:<10} {e.release_group:<14} {sources}")
    pr("")
    pr(f"Total: {sum(1 for e in entries if e.enabled)} enabled apps, "
       f"{sum(1 for e in entries if not e.enabled)} disabled")
    return 0


def _clear() -> int:
    cleaned = False
    for directory in (TEMP_DIR, BUILD_DIR):
        if directory.exists():
            shutil.rmtree(directory)
            cleaned = True

    if (build_md := Path("build.md")).exists():
        build_md.unlink()
        cleaned = True

    pr("Cleaned successfully" if cleaned else "Already clean")
    return 0


def _sigint_handler(sig: int, frame: object) -> None:
    """Graceful SIGINT handler.

    First Ctrl+C: signal workers to drain, then write a partial report.
    Second Ctrl+C: hard exit immediately.
    """
    global _shutting_down
    if _shutting_down:
        epr("Second interrupt received, exiting immediately")
        os._exit(130)

    _shutting_down = True
    _shutdown_event.set()
    mark_interrupted()
    epr("Interrupted by user, draining workers... (press Ctrl+C again to force-quit)")
    # Best-effort cleanup of partial temp files.
    for tmp in TEMP_DIR.rglob("tmp*"):
        shutil.rmtree(tmp, ignore_errors=True)
    for ks in TEMP_DIR.glob("*.keystore"):
        ks.unlink(missing_ok=True)
    # Give in-flight workers a moment to notice the interrupt flag.
    # Then exit with the conventional 130 status.
    sys.exit(130)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="apkforge",
        description="Automated APK patcher that fetches stock APKs from public mirrors, applies community patches, and signs the result.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Backwards-compatible: `apkforge Reddit arm64-v8a` is equivalent to `apkforge build Reddit arm64-v8a`.",
    )
    parser.add_argument("--version", action="store_true", help="Print version and exit")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable DEBUG-level logging")
    sub = parser.add_subparsers(dest="command")

    # build (default)
    build_p = sub.add_parser("build", help="Build patched APKs (default)")
    build_p.add_argument("target", nargs="?", default=None, help="App table name (default: all)")
    build_p.add_argument("arch", nargs="?", default=None, choices=sorted(VALID_ARCHES), help="Architecture override")

    # list
    list_p = sub.add_parser("list", help="List configured apps without building")
    list_p.add_argument("--filter", default=None, help="Case-insensitive substring filter on app table name")

    # clear
    sub.add_parser("clear", help="Remove build/, temp/ and build.md")

    return parser


def main() -> None:
    signal.signal(signal.SIGINT, _sigint_handler)
    _load_dotenv()

    parser = _build_parser()
    # If the first arg looks like a known subcommand or flag, parse normally.
    # Otherwise treat the whole invocation as a `build` (backwards-compat).
    argv = sys.argv[1:]
    if not argv or argv[0] in ("build", "list", "clear", "--version", "-v", "--verbose", "-h", "--help"):
        args = parser.parse_args(argv)
    else:
        # Bare positional form: `apkforge [target] [arch]` => `apkforge build [target] [arch]`
        args = parser.parse_args(["build", *argv])

    if args.version:
        print(_get_version())
        return
    if args.verbose:
        set_verbose(True)

    if args.command == "list":
        sys.exit(_list_apps(filter_name=args.filter))
    elif args.command == "clear":
        sys.exit(_clear())
    elif args.command == "build" or args.command is None:
        target = getattr(args, "target", None)
        arch = getattr(args, "arch", None)
        sys.exit(_build(target_app=target, arch_override=arch))
    else:
        parser.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
