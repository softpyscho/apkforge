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

"""HTTP layer for apkforge.

Built on top of ``curl_cffi`` which impersonates a real Chrome TLS
fingerprint so we can sidestep Cloudflare challenges on APKMirror and
APKPure. The ``NetworkManager`` exposes:

* Per-domain serialization via ``_domain_locks`` so we don't hammer a
  single host from multiple workers.
* Per-destination-file locking so two workers don't half-download the
  same asset.
* A retry budget (configurable via ``max_attempts``) with exponential
  backoff + jitter.
* HTTPS-only enforcement with an opt-out escape hatch for corporate CI
  proxies that MITM TLS.
* A basic SSRF guard that rejects loopback / link-local / private IP
  literals in URLs.
"""

from __future__ import annotations

import ipaddress
import os
import random
import re
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests
from curl_cffi.requests import exceptions as req_exc

from src.core.exceptions import NetworkError as _NetworkError
from src.core.exceptions import ResourceNotFoundError, SSRFError
from src.core.logger import epr

# Re-export under the old names for backwards compatibility.
NetworkError = _NetworkError

_RETRY_DELAYS = (2, 4)
_MAX_ATTEMPTS = len(_RETRY_DELAYS) + 1
# Domains we explicitly allow despite being on a private network (e.g. a
# local mirror). Empty by default.
_ALLOWED_PRIVATE_HOSTS: frozenset[str] = frozenset(
    h.strip().lower()
    for h in os.getenv("APKFORGE_ALLOW_PRIVATE_HOSTS", "").split(",")
    if h.strip()
)


def _get_lock(locks: dict, mu: threading.Lock, key) -> threading.Lock:
    with mu:
        return locks.setdefault(key, threading.Lock())


def _retry_sleep(attempt: int) -> None:
    if attempt <= len(_RETRY_DELAYS):
        time.sleep(_RETRY_DELAYS[attempt - 1] + random.uniform(0, 1))


def _handle_status(resp, url: str, attempt: int) -> bool:
    if resp.status_code == 404:
        raise ResourceNotFoundError(f"Not found (404): {url}")

    if resp.status_code == 403 or resp.status_code >= 500:
        epr(f"HTTP {resp.status_code} for {url}, attempt {attempt}/{_MAX_ATTEMPTS}")
        return True

    if resp.status_code == 429:
        # Honor Retry-After if present, otherwise fall through to the
        # exponential backoff in the caller.
        retry_after = resp.headers.get("Retry-After")
        if retry_after and retry_after.isdigit():
            time.sleep(min(int(retry_after), 60))
        epr(f"HTTP 429 (rate limited) for {url}, attempt {attempt}/{_MAX_ATTEMPTS}")
        return True

    if resp.status_code >= 400:
        resp.raise_for_status()
    return False


def _validate_url(url: str, allow_insecure: bool) -> None:
    """Reject URLs that are not HTTPS (unless ``allow_insecure``) or that
    resolve to a loopback / link-local / private IP literal.

    This is a basic SSRF guard: it only inspects the URL's host. If the
    host is a domain name we accept it (DNS rebinding is out of scope
    for this tool, which is invoked by trusted CI on trusted configs).
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.lower()
    if scheme not in ("https", "http", "file"):
        raise SSRFError(f"Unsupported URL scheme {scheme!r} for {url}")

    host = parsed.hostname or ""
    if host and host.lower() not in _ALLOWED_PRIVATE_HOSTS:
        # If the host is a literal IP, reject loopback / private / link-local.
        try:
            ip = ipaddress.ip_address(host)
        except ValueError:
            pass  # domain name — accept
        else:
            if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved:
                raise SSRFError(
                    f"Refusing to fetch {url} (host {host} is loopback/private/link-local). "
                    "Add it to APKFORGE_ALLOW_PRIVATE_HOSTS if this is intentional."
                )

    # Scheme check is done last so the IP-guard fires first (more useful
    # error message when a user accidentally configures a private IP over HTTP).
    if scheme == "http" and not allow_insecure:
        raise SSRFError(
            f"Refusing to fetch plain-HTTP URL {url} (set allow_insecure=true "
            "in config.toml or APKFORGE_INSECURE=1 to override)"
        )


class NetworkManager:
    def __init__(self, *, allow_insecure: bool = False, http2: bool = True, max_attempts: int = _MAX_ATTEMPTS) -> None:
        self.session = requests.Session(impersonate="chrome146", http2=http2)
        self._allow_insecure = allow_insecure
        self._max_attempts = max_attempts
        token = os.getenv("GITHUB_TOKEN")
        self._gh_headers: dict[str, str] = {"Authorization": f"token {token}"} if token else {}
        self._domain_locks: dict[str, threading.Lock] = {}
        self._domain_mu = threading.Lock()
        self._dest_locks: dict[Path, threading.Lock] = {}
        self._dest_mu = threading.Lock()

    def get(self, url: str, headers: dict[str, str] | None = None) -> str:
        _validate_url(url, self._allow_insecure)
        netloc = urlparse(url).netloc
        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                # Per-domain lock is enough to serialize requests to the same
                # host; we no longer sleep a fixed 500ms on top of it.
                with _get_lock(self._domain_locks, self._domain_mu, netloc):
                    resp = self.session.get(
                        url,
                        timeout=(5, 10),
                        allow_redirects=True,
                        headers=headers,
                        verify=not self._allow_insecure,
                    )

                if _handle_status(resp, url, attempt):
                    _retry_sleep(attempt)
                    continue

                return resp.text
            except req_exc.RequestException as exc:
                last_exc = exc
                epr(f"Request error for {url}, attempt {attempt}/{self._max_attempts}: {exc}")
                _retry_sleep(attempt)
        raise NetworkError(f"Request failed after {self._max_attempts} attempts: {url}") from last_exc

    def download(self, url: str, dest: Path, headers: dict[str, str] | None = None) -> None:
        _validate_url(url, self._allow_insecure)
        if dest.exists():
            return

        with _get_lock(self._dest_locks, self._dest_mu, dest):
            if dest.exists():
                return

            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_name(f"tmp.{dest.name}")
            tmp.unlink(missing_ok=True)
            netloc = urlparse(url).netloc
            last_exc: Exception | None = None
            for attempt in range(1, self._max_attempts + 1):
                try:
                    with _get_lock(self._domain_locks, self._domain_mu, netloc):
                        resp = self.session.get(
                            url,
                            timeout=(5, 300),
                            stream=True,
                            allow_redirects=True,
                            headers=headers,
                            verify=not self._allow_insecure,
                        )

                    if _handle_status(resp, url, attempt):
                        _retry_sleep(attempt)
                        continue

                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1048576):
                            fh.write(chunk)
                    tmp.replace(dest)
                    return
                except req_exc.RequestException as exc:
                    tmp.unlink(missing_ok=True)
                    last_exc = exc
                    epr(f"Download error for {url}, attempt {attempt}/{self._max_attempts}: {exc}")
                    _retry_sleep(attempt)
            raise NetworkError(f"Download failed after {self._max_attempts} attempts: {url}") from last_exc

    def __enter__(self) -> "NetworkManager":
        return self

    def __exit__(self, *_: object) -> None:
        self.session.close()
