# ---------------------------------------------------------
# Copyright (C) 2026 The apkforge Contributors
#
# DO NOT REMOVE OR ALTER THIS COPYRIGHT HEADER.
# This file is part of apkforge.
# Canonical source: https://github.com/softpyscho/apkforge
#
# Licensed under the GNU GPLv3.
# ---------------------------------------------------------

import re
import json
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from src.core.network import NetworkManager, ResourceNotFoundError
from src.scrapers.base import AppMetadata, BaseScraper, DownloadResult, ScraperError, _parse_html

class APKPureError(ScraperError):
    pass

class APKPureScraper(BaseScraper):
    def __init__(self, net: NetworkManager) -> None:
        super().__init__(net)
        self._package_name: str = ""
        self._version_urls: dict[str, str] = {}

    def fetch_metadata(self, url: str) -> AppMetadata:
        """
        Fetches the app metadata and available versions from an APKPure URL.
        Example url: https://apkpure.com/youtube/com.google.android.youtube
        """
        resp_html = self.net.get(url)
        soup = _parse_html(resp_html)
        
        # APKPure stores the package name in the URL and meta tags
        pkg_meta = soup.select_one("meta[property='product:retailer_item_id']")
        if not pkg_meta or not pkg_meta.get("content"):
            # Fallback: extract from URL
            m = re.search(r'/([^/]+)$', url)
            if not m:
                raise APKPureError("Package name not found in URL or metadata")
            self._package_name = m.group(1)
        else:
            self._package_name = pkg_meta["content"]

        # APKPure usually has a "versions" page URL formatted as: {base_url}/versions
        versions_url = url.rstrip("/") + "/versions"
        try:
            versions_html = self.net.get(versions_url)
            v_soup = _parse_html(versions_html)
        except ResourceNotFoundError:
            raise APKPureError("Failed to fetch versions page")

        versions: list[str] = []
        
        # Scrape the version history list
        # APKPure typically uses an unordered list (ul.versions-list) containing li elements
        for item in v_soup.select("ul.versions-list li, div.versions-item"):
            # Find the version number (e.g., "19.23.40")
            version_span = item.select_one(".version, .ver")
            # Find the link to the download page for this specific version
            link = item.select_one("a.download-btn, a[href*='/download']")
            
            if not version_span or not link:
                continue
                
            v_text = version_span.get_text(strip=True).replace("V", "").strip()
            
            # Skip beta/alpha versions if preferred
            if "beta" in v_text.lower() or "alpha" in v_text.lower():
                continue
                
            href = link.get("href")
            if href:
                full_url = urljoin("https://apkpure.com", href)
                self._version_urls[v_text] = full_url
                versions.append(v_text)

        if not versions:
            raise APKPureError("No versions found on the versions page")

        return AppMetadata(pkg_name=self._package_name, versions=versions)

    def download(self, url: str, version: str, dest: Path, arch: str, dpi: str) -> DownloadResult:
        """
        Navigates the download pages and fetches the actual APK/XAPK file.
        """
        release_url = self._version_urls.get(version)
        
        if release_url is None:
            raise APKPureError(f"Version {version} not found in the scraped list")

        try:
            release_html = self.net.get(release_url)
        except ResourceNotFoundError:
            raise APKPureError(f"Failed to access download page for version {version}")

        soup = _parse_html(release_html)
        
        # APKPure's final download page usually triggers the download via JS or a direct button.
        # It often contains a link with the id 'download_link' or a class 'btn-download'
        download_btn = soup.select_one("a#download_link, a.download-fallback-btn, a.btn-download.-color")
        
        if not download_btn or not download_btn.get("href"):
            # Sometimes APKPure obfuscates the link in a script tag if Cloudflare is heavy
            script_tag = soup.find(string=re.compile(r"""win\.location\.href\s*=\s*['"](https://d\.apkpure\.com[^'"]+)"""))
            if script_tag:
                m = re.search(r"""win\.location\.href\s*=\s*['"](https://d\.apkpure\.com[^'"]+)""", script_tag)
                if m:
                    final_url = m.group(1)
                else:
                    raise APKPureError("Could not extract download URL from script")
            else:
                raise APKPureError("Could not find the final download button or script")
        else:
            final_url = urljoin("https://apkpure.com", download_btn["href"])

        # Determine if it's an XAPK (APKPure's bundle format)
        is_bundle = False
        if final_url.endswith(".xapk"):
            is_bundle = True
        elif download_btn and "XAPK" in download_btn.get_text(strip=True).upper():
            is_bundle = True
            
        out_path = dest.with_suffix(".xapk") if is_bundle else dest.with_suffix(".apk")
        
        # Perform the actual file download
        self.net.download(final_url, out_path)
        
        return DownloadResult(path=out_path, is_bundle=is_bundle)
