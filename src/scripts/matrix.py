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

import json
import os
import sys
from datetime import datetime

from src.core.config import CONFIG_PATH, load_toml, parse_app_entries, parse_config
from src.core.logger import IS_GITHUB, abort, epr
from src.core.network import NetworkManager, ResourceNotFoundError


def _require_ci(script: str) -> None:
    if not IS_GITHUB:
        abort(f"'{script}' is only available in GitHub Actions")

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
    # Just return the latest release date of our repo
    try:
        rel = json.loads(net.get(f"https://api.github.com/repos/{repo}/releases/latest", headers=net._gh_headers))
        return rel.get("published_at", "") or ""
    except Exception as exc:
        epr(f"Failed to fetch our releases: {exc}")
        return ""

def _load_entries() -> list:
    data = load_toml(CONFIG_PATH)
    return parse_app_entries(data, parse_config(data))

def get_matrix() -> None:
    filter_changelog = os.getenv("FILTER_CHANGELOG", "false").lower() == "true"
    patches_sources: list[str] = []
    has_changelog_keywords = False
    is_prerelease = False
    staged: list = []
    for entry in _load_entries():
        if not entry.enabled:
            continue
        for src in entry.patches:
            if src not in patches_sources:
                patches_sources.append(src)
        if any(spec["version"] == "dev" for spec in entry.patches.values()):
            is_prerelease = True
        if entry.changelog_keywords:
            has_changelog_keywords = True
        staged.append(entry)

    changelog_text = ""
    if filter_changelog and has_changelog_keywords and patches_sources:
        with NetworkManager() as net:
            repo = os.getenv("GITHUB_REPOSITORY")
            if repo:
                our_date = _fetch_our_releases(repo, net)
                if our_date:
                    for ps in patches_sources:
                        try:
                            text, _ = _fetch_latest_release(ps, net)
                            changelog_text += text + "\n"
                        except Exception as exc:
                            epr(f"Failed to fetch changelog for '{ps}': {exc}")

    changelog_lower = changelog_text.lower()
    include: list[dict[str, str]] = []
    for entry in staged:
        if filter_changelog and entry.changelog_keywords and changelog_text and not any(kw in changelog_lower for kw in entry.changelog_keywords):
            continue
        if entry.arch == "both":
            include.extend([{"id": entry.table, "arch": "arm64-v8a"}, {"id": entry.table, "arch": "armeabi-v7a"}])
        else:
            include.append({"id": entry.table})

    if not include:
        abort("No apps found to build")
    print(json.dumps({"include": include, "prerelease": is_prerelease}, ensure_ascii=False))

def check_builds_needed(force_all: bool = False) -> None:
    seen_patches: list[str] = []
    has_dev = False
    entries = _load_entries()
    for entry in entries:
        if not entry.enabled:
            continue
        for src in entry.patches:
            if src not in seen_patches:
                seen_patches.append(src)
        if any(spec["version"] == "dev" for spec in entry.patches.values()):
            has_dev = True

    if not seen_patches:
        print(json.dumps([]))
        return

    if force_all:
        print(json.dumps(["all"]))
        return

    repo = os.getenv("GITHUB_REPOSITORY")
    if not repo:
        abort("GITHUB_REPOSITORY environment variable is not set")

    with NetworkManager() as net:
        our_date = _fetch_our_releases(repo, net)

        if not our_date:
            print(json.dumps(["all"]))
            return

        needs_build = False
        combined_changelog = ""
        for patches_source in seen_patches:
            try:
                changelog_text, upstream_date = _fetch_latest_release(patches_source, net, version="dev" if has_dev else "latest")
                combined_changelog += changelog_text + "\n"
            except ResourceNotFoundError:
                epr(f"No upstream release found for '{patches_source}', skipping")
                continue
            except Exception as exc:
                epr(f"Failed to fetch upstream release for '{patches_source}': {exc}")
                needs_build = True
                break

            if upstream_date and datetime.fromisoformat(upstream_date) > datetime.fromisoformat(our_date):
                needs_build = True

        if needs_build:
            changelog_lower = combined_changelog.lower()
            has_apps = False
            for app in entries:
                if not app.enabled:
                    continue
                if not app.changelog_keywords or any(kw in changelog_lower for kw in app.changelog_keywords):
                    has_apps = True
                    break
            if has_apps:
                print(json.dumps(["all"]))
                return

    print(json.dumps([]))

def main() -> None:
    _require_ci("matrix.py")
    match sys.argv[1:]:
        case ["get-matrix"]:
            check_builds_needed()
        case ["get-matrix-force"]:
            check_builds_needed(force_all=True)
        case ["get-build-matrix"]:
            get_matrix()
        case _:
            abort("Usage: matrix.py get-matrix | get-matrix-force | get-build-matrix")

if __name__ == "__main__":
    main()