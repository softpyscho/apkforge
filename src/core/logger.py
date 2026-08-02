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

"""Logger for apkforge.

Thin wrapper around the stdlib ``logging`` module that preserves the
project's existing public API (``pr``, ``epr``, ``wpr``, ``abort``)
so callers don't need to change. Output goes to stderr with the
familiar ``[+]``/``[-]``/``[!]`` prefixes; when running inside GitHub
Actions we additionally emit ``::error::`` / ``::warning::`` annotation
lines so failures surface in the PR diff.

To get a richer log (timestamps, file output, per-module levels),
configure the underlying logger via ``logging.basicConfig`` or by
attaching a handler to ``apkforge.logger``.
"""

from __future__ import annotations

import logging
import os
import sys
from typing import Never

IS_GITHUB = os.getenv("GITHUB_ACTIONS") == "true"
INTERRUPTED = False

_LOGGER_NAME = "apkforge"
_logger = logging.getLogger(_LOGGER_NAME)
if not _logger.handlers:
    _handler = logging.StreamHandler(sys.stderr)
    _handler.setFormatter(logging.Formatter("%(message)s"))
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)
    _logger.propagate = False


class _GitHubAnnotationFilter(logging.Filter):
    """When running on GitHub, also emit ``::level::`` annotation lines."""

    def filter(self, record: logging.LogRecord) -> bool:
        gh_level = getattr(record, "gh_level", None)
        if IS_GITHUB and gh_level:
            # Emit the annotation to stderr as a separate line so the
            # formatted message is still visible to humans.
            print(f"::{gh_level}::{record.getMessage()}", file=sys.stderr)
        return True


_handler.addFilter(_GitHubAnnotationFilter())


def is_interrupted() -> bool:
    return INTERRUPTED

def mark_interrupted() -> None:
    global INTERRUPTED
    INTERRUPTED = True

def _emit(color: str, symbol: str, msg: str, level: int, gh_level: str | None = None) -> None:
    """Emit a single log line, colorised for TTYs and annotated for GH."""
    if IS_GITHUB and gh_level:
        # On GitHub, skip the ANSI colour and emit the annotation only.
        _logger.log(level, f"[{symbol}] {msg}", extra={"gh_level": gh_level})
    else:
        _logger.log(level, f"\033[0;{color}m[{symbol}] {msg}\033[0m", extra={"gh_level": gh_level})

def pr(msg: str) -> None:
    _emit("32", "+", msg, logging.INFO)

def epr(msg: str) -> None:
    _emit("31", "-", msg, logging.ERROR, "error")

def wpr(msg: str) -> None:
    _emit("33", "!", msg, logging.WARNING, "warning")

def abort(msg: str) -> Never:
    epr(f"ABORT: {msg}")
    sys.exit(1)


def set_verbose(verbose: bool) -> None:
    """Toggle DEBUG-level output for the package logger."""
    _logger.setLevel(logging.DEBUG if verbose else logging.INFO)
