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
import sys
import urllib.parse
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8")

from src.core.logger import IS_GITHUB, abort


def _require_ci(script: str) -> None:
    if not IS_GITHUB:
        abort(f"'{script}' is only available in GitHub Actions")

def _parse_log_file(log: Path, collected: list[str]) -> str:
    microg_line = ""
    lines = [s for ln in log.read_text(encoding="utf-8").splitlines() if (s := ln.strip())]
    for i, line in enumerate(lines):
        if not microg_line and line.startswith("▶️") and "MicroG" in line:
            microg_line = line
        elif line.startswith("> ⚙️ » CLI:"):
            collected.append(f"{line}  ")
        elif line.startswith("> ⚙️ » Patches:"):
            next_line = lines[i + 1] if i + 1 < len(lines) else ""
            collected.append(f"{line}  \n{next_line}  ")
    return microg_line

def combine_logs(logs_dir: Path | str, versions_file: Path | str = "versions_info.json") -> None:
    logs = sorted(Path(logs_dir).rglob("build*.md"))
    
    # Load versions and patches info
    versions_info = {"success": []}
    v_path = Path(versions_file)
    if v_path.exists():
        versions_info = json.loads(v_path.read_text(encoding="utf-8"))
        
    patches_info = {}
    if Path("patches_info.json").exists():
        patches_info = json.loads(Path("patches_info.json").read_text(encoding="utf-8"))

    collected: list[str] = []
    microg_line = ""
    for log in logs:
        m_line = _parse_log_file(log, collected)
        if not microg_line:
            microg_line = m_line

    repo = os.getenv('GITHUB_REPOSITORY', 'softpyscho/apkforge')
    
    print("## 🚀 Built Applications\n")
    print("| App | Version | Architecture | Download & Patches |")
    print("|:---|:-------:|:------------:|:---------|")
    
    from src.core.config import CONFIG_PATH, load_toml, parse_app_entries, parse_config
    from src.scripts.readme import _patches_label
    
    data = load_toml(CONFIG_PATH)
    config = parse_config(data)
    entries = parse_app_entries(data, config)
    
    # Group entries by patch source to compute general patches
    groups = {}
    for entry in entries:
        if not entry.enabled: continue
        for source in entry.patches:
            if source not in groups: groups[source] = []
            if not any(e.table == entry.table for e in groups[source]):
                groups[source].append(entry)
                
    source_general_patches = {}
    for source, apps in groups.items():
        if len(apps) > 1:
            sets = []
            for app in apps:
                if app.table in patches_info:
                    sets.append(set(patches_info[app.table]))
            if len(sets) > 1:
                source_general_patches[source] = set.intersection(*sets)
            else:
                source_general_patches[source] = set()
        else:
            source_general_patches[source] = set()
            
    # Deduplicate versions list to prevent duplicates
    unique_success = []
    seen = set()
    for s in versions_info.get("success", []):
        key = (s.get("app"), s.get("version"), s.get("label"))
        if key not in seen:
            seen.add(key)
            unique_success.append(s)
            
    for success in unique_success:
        app = success.get("app", "")
        version = success.get("version", "")
        apk = success.get("apk", "")
        
        # Parse architecture from label if possible, or fallback
        label = success.get("label", "")
        arch = "arm64-v8a"
        if "(" in label and ")" in label:
            arch = label.split("(")[-1].strip(")")
            
        # URL encode the APK filename
        apk_encoded = urllib.parse.quote(apk)
        download_link = f"https://github.com/{repo}/releases/download/{{TAG}}/{apk_encoded}"
        
        # Get patches for this app using readme logic
        entry = next((e for e in entries if e.table == app), None)
        if entry:
            source = list(entry.patches.keys())[0] if entry.patches else None
            general_patches = source_general_patches.get(source, set())
            details_html = _patches_label(entry, patches_info, general_patches)
        else:
            app_patches = patches_info.get(app, [])
            patch_count = len(app_patches)
            patch_details = "<br>".join(app_patches)
            details_html = f"<details><summary><b>{patch_count} patches</b></summary>{patch_details}</details>" if patch_count > 0 else ""
        
        print(f"| **{app}** | `{version}` | `{arch}` | [⬇️ APK]({download_link}) {details_html} |")

    print("")
    if microg_line:
        print(microg_line, end="\n\n")

    if unique := list(dict.fromkeys(collected)):
        print("---\n### ⚙️ Patch Sources & CLI\n")
        print("\n\n".join(unique))

def main() -> None:
    match sys.argv[1:]:
        case ["combine-logs", logs_dir, versions_file]:
            combine_logs(logs_dir=Path(logs_dir), versions_file=Path(versions_file))
        case ["combine-logs", logs_dir]:
            combine_logs(logs_dir=Path(logs_dir))
        case ["combine-logs"]:
            combine_logs(logs_dir=Path("logs"))
        case _:
            abort("Usage: logs.py combine-logs [dir] [versions_file]")

if __name__ == "__main__":
    main()