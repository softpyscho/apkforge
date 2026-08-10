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

import re
from pathlib import Path
from urllib.parse import urljoin

from src.core.network import NetworkManager
from src.scrapers.base import AppMetadata, BaseScraper, DownloadResult, ScraperError, _parse_html


class DirectScraperError(ScraperError):
    pass


class DirectScraper(BaseScraper):
    def __init__(self, net: NetworkManager) -> None:
        super().__init__(net)
        self._direct_urls: dict[str, str] = {}

    def fetch_metadata(self, url: str) -> AppMetadata:
        if url.lower().split("?")[0].endswith((".apk", ".apkm", ".xapk")):
            self._direct_urls[url] = url
            return AppMetadata(pkg_name="", versions=["latest"])

        try:
            html = self.net.get(url)
            soup = _parse_html(html)
            
            # Find any link pointing to an APK file
            apk_href = None
            for a in soup.find_all("a", href=True):
                href = a["href"]
                clean_href = href.lower().split("?")[0]
                if clean_href.endswith((".apk", ".apkm", ".xapk")):
                    apk_href = href
                    break
            
            if not apk_href:
                # Regex fallback for embedded APK URLs (JS/JSON)
                match = re.search(r'https?://[^\s"\'<>]+\.(?:apk|apkm|xapk)(?:\?[^\s"\'<>]*)?', html, re.I)
                if match:
                    apk_href = match.group(0)

            if not apk_href:
                raise DirectScraperError(f"Could not find direct APK link on page '{url}'")

            direct_url = urljoin(url, apk_href)
            self._direct_urls[url] = direct_url
            return AppMetadata(pkg_name="", versions=["latest"])
        except Exception as exc:
            if isinstance(exc, DirectScraperError):
                raise
            raise DirectScraperError(f"Failed to fetch metadata from '{url}': {exc}") from exc

    def download(self, url: str, version: str, dest: Path, arch: str, dpi: str) -> DownloadResult:
        if url not in self._direct_urls:
            self.fetch_metadata(url)

        direct_url = self._direct_urls.get(url)
        if not direct_url:
            raise DirectScraperError(f"No direct URL available for '{url}'")

        is_bundle = direct_url.lower().split("?")[0].endswith((".apkm", ".xapk"))
        out_path = dest.with_suffix(".apkm") if is_bundle else dest
        self.net.download(direct_url, out_path)
        orig_name = direct_url.split("?")[0].split("/")[-1]
        return DownloadResult(path=out_path, is_bundle=is_bundle, original_name=orig_name)
