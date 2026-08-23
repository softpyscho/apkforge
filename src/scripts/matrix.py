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

import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from src.core.builder import _make_scraper, _parse_ver
from src.core.config import CONFIG_PATH, AppEntry, load_toml, parse_app_entries, parse_config
from src.core.logger import IS_GITHUB, abort, epr, pr
from src.core.network import NetworkError, NetworkManager, ResourceNotFoundError
from src.core.prebuilts import get_highest_ver
from src.scripts.wa_version import update_config_toml


def _fetch_latest_release(source: str, net: NetworkManager, version: str = "latest") -> tuple[str, str]:
    scheme, clean_src = source.split(":", 1)
    if scheme == "gitlab":
        project = clean_src.replace("/", "%2F")
        upstream_rel = json.loads(net.get(f"https://gitlab.com/api/v4/projects/{project}/releases/permalink/latest"))
        changelog_text = upstream_rel.get("description", "") or ""
        upstream_date = upstream_rel.get("released_at", "") or ""
    elif version == "dev":
        releases = json.loads(net.get(f"https://api.github.com/repos/{clean_src}/releases?per_page=1", headers=net._gh_headers))
        upstream_rel = releases[0] if releases else {}
        changelog_text = upstream_rel.get("body", "") or ""
        upstream_date = upstream_rel.get("published_at", "") or ""
    else:
        upstream_rel = json.loads(net.get(f"https://api.github.com/repos/{clean_src}/releases/latest", headers=net._gh_headers))
        changelog_text = upstream_rel.get("body", "") or ""
        upstream_date = upstream_rel.get("published_at", "") or ""
    return changelog_text, upstream_date


def _fetch_our_releases(repo: str, net: NetworkManager) -> str:
    """Return the latest published_at release date of our repo."""
    try:
        rel = json.loads(net.get(f"https://api.github.com/repos/{repo}/releases/latest", headers=net._gh_headers))
        return rel.get("published_at", "") or ""
    except Exception as exc:
        epr(f"Failed to fetch our releases: {exc}")
        return ""


def _load_entries() -> list[AppEntry]:
    data = load_toml(CONFIG_PATH)
    return parse_app_entries(data, parse_config(data))


def _load_prev_versions() -> dict[str, str]:
    """Load previously built versions mapping from versions_info.json."""
    prev: dict[str, str] = {}
    p = Path("versions_info.json")
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            for item in data.get("success", []):
                if "app" in item and "version" in item:
                    prev[item["app"]] = item["version"]
        except Exception:
            pass
    return prev


def _clean_version_string(v: str) -> str:
    if not v:
        return ""
    m = re.search(r"(\d+\.\d+(?:\.\d+)+(?:-[a-zA-Z0-9.]+)?)\b", v)
    if m:
        return m.group(1)
    return v.strip()


def _is_newer_version(new_ver: str, prev_ver: str | None) -> bool:
    if not new_ver or new_ver in ("latest", "auto", "nightly"):
        return False
    if not prev_ver:
        return True
    c_new = _clean_version_string(new_ver)
    c_prev = _clean_version_string(prev_ver)
    if not c_new or c_new in ("latest", "auto", "nightly"):
        return False
    try:
        return _parse_ver(c_new) > _parse_ver(c_prev)
    except Exception:
        return c_new != c_prev


def _check_app_needs_update(
    entry: AppEntry,
    prev_version: str | None,
    our_date: str,
    patch_cache: dict[tuple[str, str], tuple[str, str]],
    net: NetworkManager,
) -> bool:
    """Determine if a single app entry needs to be built."""
    if not prev_version:
        pr(f"[*] App '{entry.table}' has no previous build record -> update needed.")
        return True

    # 1. WhatsApp / WhatsApp-Business version check
    if entry.table in ("WhatsApp", "WhatsApp-Business"):
        prefix = entry.version[:-3] if entry.version.endswith(".xx") else entry.version
        if not prev_version.startswith(f"{prefix}."):
            pr(f"[*] {entry.table} version '{prev_version}' does not match target prefix '{prefix}' -> update needed.")
            return True
        # If prefix matches, still check online scrapers for newer minor builds (e.g. 2.26.32.79 -> 2.26.32.80)
        for src, url in entry.dl_urls.items():
            try:
                scraper = _make_scraper(src, net)
                meta = scraper.fetch_metadata(url)
                matching = [v for v in meta.versions if v.startswith(f"{prefix}.")]
                highest = get_highest_ver(matching) if matching else None
                if highest and _is_newer_version(highest, prev_version):
                    pr(f"[*] {entry.table} newer version '{highest}' available (prev: '{prev_version}') -> update needed.")
                    return True
                break
            except Exception:
                continue
        return False

    # 2. Duck Detector or GitHub release based mirror
    if entry.mirror and entry.version == "nightly" and "github" in entry.dl_urls:
        gh_url = entry.dl_urls["github"]
        m = re.search(r"github\.com/([^/]+/[^/]+)/releases/tag/([^/]+)", gh_url)
        if m:
            upstream_repo, tag = m.group(1), m.group(2)
            try:
                rel = json.loads(net.get(f"https://api.github.com/repos/{upstream_repo}/releases/tags/{tag}", headers=net._gh_headers))
                pub_at = rel.get("published_at", "") or ""
                if pub_at and our_date and datetime.fromisoformat(pub_at) > datetime.fromisoformat(our_date):
                    pr(f"[*] {entry.table} upstream nightly release published at {pub_at} > our release {our_date} -> update needed.")
                    return True
            except Exception as exc:
                epr(f"Failed to check upstream release for '{entry.table}': {exc}")
        return False

    # 3. Generic Mirror Apps
    if entry.mirror:
        # Check if a newer version is available online
        for src, url in entry.dl_urls.items():
            try:
                scraper = _make_scraper(src, net)
                meta = scraper.fetch_metadata(url)
                if meta.versions:
                    highest = get_highest_ver(meta.versions)
                    if highest and _is_newer_version(highest, prev_version):
                        pr(f"[*] {entry.table} newer version '{highest}' available online (prev: '{prev_version}') -> update needed.")
                        return True
                    break
            except Exception:
                continue
        return False

    # 4. Patched Apps: Check Patch Sources
    for src, spec in entry.patches.items():
        ver_spec = spec.get("version", "latest") if isinstance(spec, dict) else "latest"
        cache_key = (src, ver_spec)
        if cache_key not in patch_cache:
            try:
                patch_cache[cache_key] = _fetch_latest_release(src, net, version=ver_spec)
            except Exception as exc:
                epr(f"Failed to fetch patch source '{src}': {exc}")
                patch_cache[cache_key] = ("", "")

        changelog_text, upstream_date = patch_cache[cache_key]
        if upstream_date and our_date and datetime.fromisoformat(upstream_date) > datetime.fromisoformat(our_date):
            if entry.changelog_keywords:
                changelog_lower = changelog_text.lower()
                if any(kw in changelog_lower for kw in entry.changelog_keywords):
                    pr(f"[*] {entry.table} patch source '{src}' updated with matching keywords -> update needed.")
                    return True
            else:
                pr(f"[*] {entry.table} patch source '{src}' updated at {upstream_date} -> update needed.")
                return True

    # 5. Patched Apps: Check APK Version update for "latest" or fixed/wildcard
    if entry.version == "latest":
        for src, url in entry.dl_urls.items():
            try:
                scraper = _make_scraper(src, net)
                meta = scraper.fetch_metadata(url)
                if meta.versions:
                    highest = get_highest_ver(meta.versions)
                    if highest and _is_newer_version(highest, prev_version):
                        pr(f"[*] {entry.table} newer APK version '{highest}' available online (prev: '{prev_version}') -> update needed.")
                        return True
                    break
            except Exception:
                continue
    elif entry.version != "auto":
        prefix = entry.version[:-3] if entry.version.endswith(".xx") else entry.version
        if not prev_version.startswith(prefix):
            pr(f"[*] {entry.table} target version '{entry.version}' != prev version '{prev_version}' -> update needed.")
            return True

    return False


def get_apps_to_build(force_all: bool = False, apps_filter: list[str] | None = None) -> list[AppEntry]:
    """Calculate the list of AppEntry objects that need to be built."""
    # Ensure WhatsApp versions in config.toml are synchronized with WaEnhancer
    try:
        update_config_toml()
    except Exception as exc:
        epr(f"Warning: Failed to update WaEnhancer versions: {exc}")

    entries = _load_entries()
    enabled_entries = [e for e in entries if e.enabled]

    if apps_filter:
        filter_set = {a.strip().lower() for a in apps_filter if a.strip()}
        return [e for e in enabled_entries if e.table.lower() in filter_set or e.app_name.lower() in filter_set]

    if force_all:
        return enabled_entries

    prev_versions = _load_prev_versions()
    repo = os.getenv("GITHUB_REPOSITORY", "softpyscho/apkforge")

    apps_to_build: list[AppEntry] = []
    patch_cache: dict[tuple[str, str], tuple[str, str]] = {}

    with NetworkManager() as net:
        our_date = _fetch_our_releases(repo, net) if IS_GITHUB or os.getenv("GITHUB_TOKEN") else ""

        for entry in enabled_entries:
            prev_ver = prev_versions.get(entry.table)
            if _check_app_needs_update(entry, prev_ver, our_date, patch_cache, net):
                apps_to_build.append(entry)

    return apps_to_build


def build_matrix_json(apps: list[AppEntry]) -> str:
    """Format AppEntry list into GitHub Actions matrix JSON."""
    include: list[dict[str, str]] = []
    is_prerelease = False

    for entry in apps:
        if any(isinstance(spec, dict) and spec.get("version") == "dev" for spec in entry.patches.values()):
            is_prerelease = True

        if entry.arch == "both":
            include.extend([
                {"id": entry.table, "arch": "arm64-v8a"},
                {"id": entry.table, "arch": "armeabi-v7a"},
            ])
        else:
            item = {"id": entry.table}
            if entry.arch != "all":
                item["arch"] = entry.arch
            include.append(item)

    return json.dumps({"include": include, "prerelease": is_prerelease}, ensure_ascii=False)


def main() -> None:
    match sys.argv[1:]:
        case ["get-matrix"]:
            force = os.getenv("FORCE_BUILD", "false").lower() == "true"
            apps_env = os.getenv("APPS_INPUT", "").strip() or os.getenv("APPS", "").strip()
            apps_filter = [a.strip() for a in apps_env.split(",") if a.strip()] if apps_env else None
            apps = get_apps_to_build(force_all=force, apps_filter=apps_filter)
            print(build_matrix_json(apps))

        case ["get-matrix-force"]:
            apps = get_apps_to_build(force_all=True)
            print(build_matrix_json(apps))

        case ["get-build-matrix"]:
            force = os.getenv("FORCE_BUILD", "false").lower() == "true"
            apps_env = os.getenv("APPS_INPUT", "").strip() or os.getenv("APPS", "").strip()
            apps_filter = [a.strip() for a in apps_env.split(",") if a.strip()] if apps_env else None
            apps = get_apps_to_build(force_all=force, apps_filter=apps_filter)
            if not apps:
                # If no apps found, return empty include
                print(json.dumps({"include": [], "prerelease": False}))
            else:
                print(build_matrix_json(apps))

        case _:
            abort("Usage: matrix.py get-matrix | get-matrix-force | get-build-matrix")


if __name__ == "__main__":
    main()