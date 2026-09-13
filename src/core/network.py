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
import random
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests
from curl_cffi.requests import exceptions as req_exc

from src.core.logger import epr

# Impersonation targets are tried in order; on a bot challenge the next one is used.
# curl_cffi applies a matching TLS fingerprint and User-Agent for each target, so the
# User-Agent header must not be overridden manually (FlareSolverr excepted).
_DEFAULT_IMPERSONATIONS = ("chrome136", "chrome131", "firefox135", "edge101", "safari184")
_RETRY_DELAYS = (2, 4, 8)
_MAX_ATTEMPTS = len(_RETRY_DELAYS) + 1
_CHALLENGE_STATUS = frozenset({403, 429, 503})
_CHALLENGE_MARKERS = (
    "just a moment",
    "cf-chl",
    "checking your browser",
    "attention required",
    "enable javascript and cookies to continue",
)
# Markers/headers that only a JavaScript-capable browser can solve (Cloudflare managed
# challenge / Turnstile). A proxy or impersonation rotation cannot get past these.
_MANAGED_MARKERS = (
    "just a moment",
    "cf-chl",
    "checking your browser",
    "enable javascript and cookies to continue",
)
_NAV_ACCEPT = "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"

# Hosts that never need a Cloudflare warm-up.
_NO_PRIME_SUFFIXES = ("github.com", "githubusercontent.com", "gitlab.com")


class NetworkError(Exception):
    pass

class ResourceNotFoundError(NetworkError):
    """Raised when a remote resource returns HTTP 404."""

def _get_lock(locks: dict, mu: threading.Lock, key) -> threading.Lock:
    with mu:
        return locks.setdefault(key, threading.Lock())

def _proxy_config() -> dict[str, str] | None:
    """Return proxy settings from the environment (APKFORGE_PROXY takes priority)."""
    explicit = (os.getenv("APKFORGE_PROXY") or "").strip()
    if explicit:
        return {"http": explicit, "https": explicit}
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        value = (os.getenv(var) or "").strip()
        if value:
            return {"http": value, "https": value}
    return None

def _retry_after(resp) -> float | None:
    raw = resp.headers.get("Retry-After")
    if not raw:
        return None
    with contextlib.suppress(ValueError):
        return max(0.0, float(raw))
    return None

def _retry_sleep(attempt: int, retry_after: float | None = None) -> None:
    base = _RETRY_DELAYS[attempt - 1] if attempt <= len(_RETRY_DELAYS) else _RETRY_DELAYS[-1]
    delay = base + random.uniform(0, 1)
    if retry_after:
        delay = max(delay, min(retry_after, 30.0))
    time.sleep(delay)

def _is_challenge(resp, read_body: bool = True) -> bool:
    """Detect a Cloudflare/bot-protection interstitial or block.

    ``server: cloudflare`` is deliberately *not* treated as a challenge on its own:
    Cloudflare fronts plenty of legitimate 403/429/503 responses, and wrongly sending
    those down the warm-up/rotation/browser-solve path wastes the retry budget. Only
    explicit challenge signals (``cf-mitigated`` or an interstitial body) qualify.
    """
    if resp.status_code not in _CHALLENGE_STATUS:
        return False
    if resp.headers.get("cf-mitigated"):
        return True
    if not read_body:
        return False
    content_type = (resp.headers.get("content-type") or "").lower()
    if "text/html" not in content_type:
        return False
    with contextlib.suppress(Exception):
        head = (resp.text or "")[:4096].lower()
        return any(marker in head for marker in _CHALLENGE_MARKERS)
    return False

def _is_managed_challenge(resp, read_body: bool = True) -> bool:
    """True when only a JavaScript-capable browser (FlareSolverr) can get past it."""
    if resp.headers.get("cf-mitigated"):
        return True
    if not read_body:
        return False
    content_type = (resp.headers.get("content-type") or "").lower()
    if "text/html" not in content_type:
        return False
    with contextlib.suppress(Exception):
        head = (resp.text or "")[:4096].lower()
        return any(marker in head for marker in _MANAGED_MARKERS)
    return False

class _ReadWriteLock:
    """A writer-preferring reader/writer lock.

    Requests hold the read side so they may run concurrently; rotating the
    impersonation takes the write side, waiting for in-flight requests to drain before
    swapping (and closing) the session.
    """

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._readers = 0
        self._writer = False
        self._waiting_writers = 0

    @contextlib.contextmanager
    def read(self):
        with self._cond:
            while self._writer or self._waiting_writers:
                self._cond.wait()
            self._readers += 1
        try:
            yield
        finally:
            with self._cond:
                self._readers -= 1
                if self._readers == 0:
                    self._cond.notify_all()

    @contextlib.contextmanager
    def write(self):
        with self._cond:
            self._waiting_writers += 1
            while self._writer or self._readers:
                self._cond.wait()
            self._waiting_writers -= 1
            self._writer = True
        try:
            yield
        finally:
            with self._cond:
                self._writer = False
                self._cond.notify_all()

def _handle_status(resp, url: str, attempt: int) -> bool:
    if resp.status_code == 404:
        raise ResourceNotFoundError(f"Not found (404): {url}")

    if resp.status_code in (403, 410, 429) or resp.status_code >= 500:
        epr(f"HTTP {resp.status_code} for {url}, attempt {attempt}/{_MAX_ATTEMPTS}")
        return True

    if resp.status_code >= 400:
        resp.raise_for_status()
    return False

class NetworkManager:
    def __init__(self) -> None:
        self._impersonations = list(_DEFAULT_IMPERSONATIONS)
        self._imp_index = 0
        self._proxies = _proxy_config()
        self._rw = _ReadWriteLock()
        self._solved_ua: str | None = None
        self.session = self._build_session()
        token = (os.getenv("GITHUB_TOKEN") or "").strip()
        self._gh_headers: dict[str, str] = {"Authorization": f"token {token}"} if token else {}
        self._domain_locks: dict[str, threading.Lock] = {}
        self._domain_mu = threading.Lock()
        self._dest_locks: dict[Path, threading.Lock] = {}
        self._dest_mu = threading.Lock()
        self._primed: set[str] = set()
        if self._proxies:
            epr("Using proxy from environment for all requests")

    def _build_session(self):
        target = self._impersonations[self._imp_index]
        try:
            return requests.Session(impersonate=target, proxies=self._proxies)
        except Exception as exc:
            epr(f"Impersonation '{target}' unavailable ({exc}); falling back to default TLS")
            return requests.Session(proxies=self._proxies)

    def _rotate_impersonation(self) -> bool:
        if len(self._impersonations) <= 1:
            return False
        with self._rw.write():
            self._imp_index = (self._imp_index + 1) % len(self._impersonations)
            old = self.session
            self.session = self._build_session()
        with contextlib.suppress(Exception):
            old.close()
        return True

    @property
    def impersonate(self) -> str:
        return self._impersonations[self._imp_index]

    @property
    def gh_headers(self) -> dict[str, str]:
        """GitHub API headers (may include an Authorization token)."""
        return dict(self._gh_headers)

    def _browser_headers(self, url: str, extra: dict[str, str] | None, download: bool) -> dict[str, str]:
        headers = dict(extra) if extra else {}
        if self._solved_ua:
            headers.setdefault("User-Agent", self._solved_ua)
        if download:
            headers.setdefault("Accept", "*/*")
        else:
            referer = headers.get("Referer")
            headers.setdefault("Accept", _NAV_ACCEPT)
            headers.setdefault("Upgrade-Insecure-Requests", "1")
            headers.setdefault("Sec-Fetch-Dest", "document")
            headers.setdefault("Sec-Fetch-Mode", "navigate")
            if not referer:
                site = "none"
            elif urlparse(referer).netloc == urlparse(url).netloc:
                site = "same-origin"
            else:
                site = "cross-site"
            headers.setdefault("Sec-Fetch-Site", site)
            headers.setdefault("Sec-Fetch-User", "?1")
        headers.setdefault("Accept-Language", "en-US,en;q=0.9")
        return headers

    def _should_prime(self, netloc: str) -> bool:
        return not any(netloc.endswith(suffix) for suffix in _NO_PRIME_SUFFIXES)

    def _prime_domain_locked(self, url: str) -> None:
        parsed = urlparse(url)
        root = f"{parsed.scheme}://{parsed.netloc}/"
        try:
            with self._rw.read():
                self.session.get(root, timeout=(5, 10), allow_redirects=True, headers=self._browser_headers(root, None, False), verify=True)
        except Exception as exc:
            epr(f"Warm-up request for {parsed.netloc} failed: {exc}")

    def _apply_flaresolverr(self, solution: dict) -> None:
        with self._rw.read():
            for cookie in solution.get("cookies") or []:
                name, value = cookie.get("name"), cookie.get("value")
                if not name:
                    continue
                with contextlib.suppress(Exception):
                    self.session.cookies.set(name, value, domain=cookie.get("domain") or "", path=cookie.get("path") or "/")
            # The solved browser's User-Agent must accompany its `cf_clearance` cookie,
            # so it is applied per-request via `_browser_headers` rather than mutating
            # the shared session default.
            if ua := solution.get("userAgent"):
                self._solved_ua = ua

    def _flaresolverr_get(self, url: str, return_only_cookies: bool = False) -> str | None:
        """Solve a challenge through a FlareSolverr instance when configured.

        Returns the solved HTML, or an empty string when ``return_only_cookies`` is set
        and the cookies were refreshed, or ``None`` when there is no solver/no solution.
        """
        fs_url = (os.getenv("FLARESOLVERR_URL") or "").strip()
        if not fs_url:
            return None
        payload: dict[str, object] = {"cmd": "request.get", "url": url, "maxTimeout": 60000}
        if return_only_cookies:
            payload["returnOnlyCookies"] = True
        try:
            resp = requests.post(f"{fs_url.rstrip('/')}/v1", json=payload, timeout=(5, 75))
            data = resp.json()
        except Exception as exc:
            epr(f"FlareSolverr request failed: {exc}")
            return None
        if data.get("status") != "ok":
            epr(f"FlareSolverr could not solve {url}: {data.get('message', data.get('status'))}")
            return None
        solution = data.get("solution") or {}
        self._apply_flaresolverr(solution)
        if return_only_cookies:
            return ""
        return solution.get("response") or ""

    def _mitigate_challenge(self, url: str, netloc: str) -> bool:
        """Warm up cookies or rotate impersonation. Returns True when a retry is worthwhile."""
        if self._should_prime(netloc) and netloc not in self._primed:
            self._primed.add(netloc)
            with _get_lock(self._domain_locks, self._domain_mu, netloc):
                self._prime_domain_locked(url)
            epr(f"Warmed up cookies for {netloc} after a bot challenge")
            return True
        if self._rotate_impersonation():
            epr(f"Rotated impersonation to '{self.impersonate}' after a bot challenge")
            return True
        return False

    def _attempt_mitigation(self, url: str, netloc: str, managed: bool, want_html: bool) -> str | None:
        """Try to get past a challenge, cheapest mitigation first.

        Managed challenges (Cloudflare/Turnstile) can only be solved in a browser, so
        FlareSolverr is tried for those. For unclassified blocks the cookie warm-up /
        impersonation rotation is tried first, with FlareSolverr as a last resort.

        Returns the solved HTML, ``""`` when only cookies were refreshed, or ``None``
        when nothing could be done.
        """
        only_cookies = not want_html
        if managed:
            solved = self._flaresolverr_get(url, return_only_cookies=only_cookies)
            if solved is not None:
                return solved
        if self._mitigate_challenge(url, netloc):
            return ""
        if not managed:
            solved = self._flaresolverr_get(url, return_only_cookies=only_cookies)
            if solved is not None:
                return solved
        return None

    def get(self, url: str, headers: dict[str, str] | None = None) -> str:
        netloc = urlparse(url).netloc
        last_exc: Exception | None = None
        req_headers = self._browser_headers(url, headers, download=False)
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                with _get_lock(self._domain_locks, self._domain_mu, netloc):
                    time.sleep(0.5)
                    with self._rw.read():
                        resp = self.session.get(url, timeout=(5, 15), allow_redirects=True, headers=req_headers, verify=True)

                        if resp.status_code == 401 and "Authorization" in req_headers:
                            req_headers.pop("Authorization", None)
                            resp = self.session.get(url, timeout=(5, 15), allow_redirects=True, headers=req_headers, verify=True)

                if _is_challenge(resp):
                    last_exc = NetworkError(f"Bot challenge for {url}")
                    managed = _is_managed_challenge(resp)
                    solved_html = self._attempt_mitigation(url, netloc, managed, want_html=True)
                    if solved_html:
                        return solved_html
                    if solved_html == "":
                        _retry_sleep(attempt)
                        continue
                    epr(f"Bot challenge for {url} could not be bypassed. Configure APKFORGE_PROXY or FLARESOLVERR_URL to get past it")
                    break

                if _handle_status(resp, url, attempt):
                    _retry_sleep(attempt, _retry_after(resp))
                    continue

                return resp.text
            except req_exc.RequestException as exc:
                last_exc = exc
                epr(f"Request error for {url}, attempt {attempt}/{_MAX_ATTEMPTS}: {exc}")
                _retry_sleep(attempt)
        raise NetworkError(f"Request failed after {_MAX_ATTEMPTS} attempts: {url}") from last_exc

    def download(self, url: str, dest: Path, headers: dict[str, str] | None = None) -> None:
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
            req_headers = self._browser_headers(url, headers, download=True)
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    challenge = False
                    managed = False
                    retry_after: float | None = None
                    with _get_lock(self._domain_locks, self._domain_mu, netloc):
                        time.sleep(0.5)
                        with self._rw.read():
                            resp = self.session.get(url, timeout=(5, 300), stream=True, allow_redirects=True, headers=req_headers, verify=True)

                            if resp.status_code == 401 and "Authorization" in req_headers:
                                req_headers.pop("Authorization", None)
                                resp = self.session.get(url, timeout=(5, 300), stream=True, allow_redirects=True, headers=req_headers, verify=True)

                            if _is_challenge(resp, read_body=False):
                                challenge = True
                                managed = _is_managed_challenge(resp, read_body=False)
                                resp.close()
                            elif not _handle_status(resp, url, attempt):
                                with tmp.open("wb") as fh:
                                    for chunk in resp.iter_content(chunk_size=1048576):
                                        fh.write(chunk)
                                resp.close()
                                tmp.replace(dest)
                                return
                            else:
                                retry_after = _retry_after(resp)
                                resp.close()

                    if challenge:
                        last_exc = NetworkError(f"Bot challenge for {url}")
                        # Solve the real download URL so the refreshed cookies (and any
                        # Turnstile clearance) apply to this resource, with
                        # `returnOnlyCookies` to avoid pulling the binary through the solver.
                        solved = self._attempt_mitigation(url, netloc, managed, want_html=False)
                        if solved is None:
                            epr(f"Bot challenge for {url} could not be bypassed. Configure APKFORGE_PROXY or FLARESOLVERR_URL to get past it")
                            break
                        _retry_sleep(attempt)
                        continue

                    _retry_sleep(attempt, retry_after)
                except req_exc.RequestException as exc:
                    tmp.unlink(missing_ok=True)
                    last_exc = exc
                    epr(f"Download error for {url}, attempt {attempt}/{_MAX_ATTEMPTS}: {exc}")
                    _retry_sleep(attempt)
            tmp.unlink(missing_ok=True)
            raise NetworkError(f"Download failed after {_MAX_ATTEMPTS} attempts: {url}") from last_exc

    def __enter__(self) -> "NetworkManager":
        return self

    def __exit__(self, *_: object) -> None:
        self.session.close()