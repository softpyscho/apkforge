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
import urllib.parse
from pathlib import Path

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

def combine_logs(logs_dir: Path | str) -> None:
    logs = sorted(Path(logs_dir).rglob("build*.md"))
    
    # Load versions and patches info
    versions_info = {"success": []}
    if Path("versions_info.json").exists():
        versions_info = json.loads(Path("versions_info.json").read_text(encoding="utf-8"))
        
    patches_info = {}
    if Path("patches_info.json").exists():
        patches_info = json.loads(Path("patches_info.json").read_text(encoding="utf-8"))

    collected: list[str] = []
    microg_line = ""
    for log in logs:
        m_line = _parse_log_file(log, collected)
        if not microg_line:
            microg_line = m_line

    repo = os.getenv('GITHUB_REPOSITORY', 'softpsycho/apkforge')
    
    print("## 🚀 Built Applications\n")
    print("| App | Version | Architecture | Download & Patches |")
    print("|:---|:-------:|:------------:|:---------|")
    
    for success in versions_info.get("success", []):
        app = success.get("app", "")
        version = success.get("version", "")
        apk = success.get("apk", "")
        
        # Parse architecture from label if possible, or fallback
        label = success.get("label", "")
        arch = "arm64-v8a"
        if "(" in label and ")" in label:
            arch = label.split("(")[-1].strip(")")
            
        # Get patches for this app
        app_patches = patches_info.get(app, [])
        patch_count = len(app_patches)
        patch_details = "<br>".join(app_patches)
        
        # URL encode the APK filename
        apk_encoded = urllib.parse.quote(apk)
        download_link = f"https://github.com/{repo}/releases/download/{{TAG}}/{apk_encoded}"
        
        details_html = f"<details><summary><b>{patch_count} patches</b></summary>{patch_details}</details>" if patch_count > 0 else ""
        
        print(f"| **{app}** | `{version}` | `{arch}` | [⬇️ APK]({download_link}) {details_html} |")

    print("")
    if microg_line:
        print(microg_line, end="\n\n")

    if unique := list(dict.fromkeys(collected)):
        print("---\n### ⚙️ Patch Sources & CLI\n")
        print("\n\n".join(unique))

def main() -> None:
    _require_ci("logs.py")
    match sys.argv[1:]:
        case ["combine-logs", *args]:
            combine_logs(logs_dir=Path(args[0] if args else "logs"))
        case _:
            abort("Usage: logs.py combine-logs [dir]")

if __name__ == "__main__":
    main()