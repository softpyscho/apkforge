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

import os
import random
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

from curl_cffi import requests
from curl_cffi.requests import exceptions as req_exc

from src.core.logger import epr

_RETRY_DELAYS = (2, 4)
_MAX_ATTEMPTS = len(_RETRY_DELAYS) + 1


class NetworkError(Exception):
    pass

class ResourceNotFoundError(NetworkError):
    """Raised when a remote resource returns HTTP 404."""

def _get_lock(locks: dict, mu: threading.Lock, key) -> threading.Lock:
    with mu:
        return locks.setdefault(key, threading.Lock())

def _retry_sleep(attempt: int) -> None:
    if attempt <= len(_RETRY_DELAYS):
        time.sleep(_RETRY_DELAYS[attempt - 1] + random.uniform(0, 1))

def _handle_status(resp, url: str, attempt: int) -> bool:
    if resp.status_code == 404:
        raise ResourceNotFoundError(f"Not found (404): {url}")

    if resp.status_code in (403, 410, 429) or resp.status_code >= 500:
        # Check for Cloudflare Turnstile challenge
        is_cf = "cloudflare" in str(resp.headers.get("server", "")).lower() and ("just a moment" in resp.text.lower() or "challenge-platform" in resp.text.lower())
        if resp.status_code == 403 and is_cf:
            epr(f"Cloudflare challenge encountered for {url}. Provide CF_CLEARANCE / APKMIRROR_COOKIE to bypass.")
            raise NetworkError(f"Cloudflare challenge (403) for {url}")

        epr(f"HTTP {resp.status_code} for {url}, attempt {attempt}/{_MAX_ATTEMPTS}")
        return True

    if resp.status_code >= 400:
        resp.raise_for_status()
    return False

def _load_dotenv(path: Path = Path(".env")) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        if key and key not in os.environ:
            os.environ[key] = value.strip().strip('"\'')

class NetworkManager:
    def __init__(self) -> None:
        _load_dotenv()
        self.session = requests.Session(impersonate="chrome131")
        
        # Load custom APKMirror cookies if provided in environment / .env
        cf_clearance = (os.getenv("CF_CLEARANCE") or "").strip()
        apkmirror_cookie = (os.getenv("APKMIRROR_COOKIE") or "").strip()
        if cf_clearance:
            self.session.cookies.set("cf_clearance", cf_clearance, domain=".apkmirror.com")
        elif apkmirror_cookie:
            for part in apkmirror_cookie.split(";"):
                if "=" in part:
                    k, _, v = part.strip().partition("=")
                    self.session.cookies.set(k.strip(), v.strip(), domain=".apkmirror.com")

        custom_ua = (os.getenv("APKMIRROR_USER_AGENT") or "").strip()
        if custom_ua:
            self.session.headers["User-Agent"] = custom_ua

        token = (os.getenv("GITHUB_TOKEN") or "").strip()
        self._gh_headers: dict[str, str] = {"Authorization": f"token {token}"} if token else {}
        self._domain_locks: dict[str, threading.Lock] = {}
        self._domain_mu = threading.Lock()
        self._dest_locks: dict[Path, threading.Lock] = {}
        self._dest_mu = threading.Lock()

    def _prepare_headers(self, url: str, headers: dict[str, str] | None) -> dict[str, str]:
        req_headers = dict(headers) if headers else {}
        if "apkmirror.com" in url.lower():
            cf_clearance = (os.getenv("CF_CLEARANCE") or "").strip()
            apkmirror_cookie = (os.getenv("APKMIRROR_COOKIE") or "").strip()
            cookie_val = cf_clearance and f"cf_clearance={cf_clearance}" or apkmirror_cookie
            if cookie_val:
                if "Cookie" in req_headers:
                    if cookie_val not in req_headers["Cookie"]:
                        req_headers["Cookie"] = f"{req_headers['Cookie']}; {cookie_val}"
                else:
                    req_headers["Cookie"] = cookie_val

            if "Referer" not in req_headers:
                req_headers["Referer"] = "https://www.apkmirror.com/"

            custom_ua = (os.getenv("APKMIRROR_USER_AGENT") or "").strip()
            if custom_ua:
                req_headers["User-Agent"] = custom_ua

        return req_headers

    def get(self, url: str, headers: dict[str, str] | None = None) -> str:
        netloc = urlparse(url).netloc
        last_exc: Exception | None = None
        req_headers = self._prepare_headers(url, headers)
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                with _get_lock(self._domain_locks, self._domain_mu, netloc):
                    time.sleep(0.5)
                    resp = self.session.get(url, timeout=(5, 10), allow_redirects=True, headers=req_headers or None, verify=True)

                if resp.status_code == 401 and req_headers and "Authorization" in req_headers:
                    req_headers.pop("Authorization", None)
                    with _get_lock(self._domain_locks, self._domain_mu, netloc):
                        resp = self.session.get(url, timeout=(5, 10), allow_redirects=True, headers=req_headers or None, verify=True)

                if _handle_status(resp, url, attempt):
                    _retry_sleep(attempt)
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
            req_headers = self._prepare_headers(url, headers)
            for attempt in range(1, _MAX_ATTEMPTS + 1):
                try:
                    with _get_lock(self._domain_locks, self._domain_mu, netloc):
                        time.sleep(0.5)
                        resp = self.session.get(url, timeout=(5, 300), stream=True, allow_redirects=True, headers=req_headers or None, verify=True)

                    if resp.status_code == 401 and req_headers and "Authorization" in req_headers:
                        req_headers.pop("Authorization", None)
                        with _get_lock(self._domain_locks, self._domain_mu, netloc):
                            resp = self.session.get(url, timeout=(5, 300), stream=True, allow_redirects=True, headers=req_headers or None, verify=True)

                    if _handle_status(resp, url, attempt):
                        _retry_sleep(attempt)
                        continue

                    with tmp.open("wb") as fh:
                        for chunk in resp.iter_content(chunk_size=1048576):
                            fh.write(chunk)
                    resp.close()
                    tmp.replace(dest)
                    return
                except req_exc.RequestException as exc:
                    tmp.unlink(missing_ok=True)
                    last_exc = exc
                    epr(f"Download error for {url}, attempt {attempt}/{_MAX_ATTEMPTS}: {exc}")
                    _retry_sleep(attempt)
            raise NetworkError(f"Download failed after {_MAX_ATTEMPTS} attempts: {url}") from last_exc

    def __enter__(self) -> "NetworkManager":
        return self

    def __exit__(self, *_: object) -> None:
        self.session.close()