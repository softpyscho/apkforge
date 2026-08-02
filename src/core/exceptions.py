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

"""Unified exception taxonomy for apkforge.

Every domain-specific exception class declares a ``retryable`` class
attribute so callers can branch on ``except ApkforgeError as exc:
if exc.retryable: ...`` rather than maintaining a fragile tuple of
types. The existing ``BuilderError``, ``PatcherError``, ``ScraperError``
etc. classes still exist (and still subclass ``ApkforgeError``) so
existing catch sites keep working.
"""

from __future__ import annotations


class ApkforgeError(Exception):
    """Base class for every apkforge-specific exception.

    Subclasses set ``retryable = True`` if a transient failure (network
    blip, rate limit, mirror 5xx) might recover on retry. Set
    ``retryable = False`` for logic errors (bad config, missing patch).
    """

    retryable: bool = False


class ConfigError(ApkforgeError):
    """Raised when ``config.toml`` is malformed or references unknown entities."""

    retryable = False


class NetworkError(ApkforgeError):
    """Raised when a network request fails after exhausting retries."""

    retryable = True


class ResourceNotFoundError(NetworkError):
    """Raised when a remote resource returns HTTP 404."""

    retryable = False


class SSRFError(NetworkError):
    """Raised when a URL points at a forbidden (loopback / private) host."""

    retryable = False


class ScraperError(ApkforgeError):
    """Raised for scraper-layer failures (DOM parsing, missing assets)."""

    retryable = False


class PrebuiltsError(ApkforgeError):
    """Raised when fetching CLI / patch bundle prebuilts fails."""

    retryable = True


class PatcherError(ApkforgeError):
    """Raised when the Morphe CLI returns a non-zero exit."""

    retryable = False


class SignatureError(PatcherError):
    """Raised when sig.txt has no entry for a package, or apksigner reports a hash mismatch."""

    retryable = False


class BuilderError(ApkforgeError):
    """Raised for orchestration-level failures in the build pipeline."""

    retryable = False
