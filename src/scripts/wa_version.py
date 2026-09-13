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
import sys
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

WAENHANCER_ARRAYS_URL = "https://raw.githubusercontent.com/Dev4Mod/WaEnhancer/master/app/src/main/res/values/arrays.xml"
CONFIG_PATH = Path("config.toml")


def fetch_recommended_wa_versions() -> tuple[str, str]:
    """Fetch the highest recommended WhatsApp and WhatsApp Business versions from WaEnhancer repo."""
    req = urllib.request.Request(WAENHANCER_ARRAYS_URL, headers={"User-Agent": "Mozilla/5.0"})
    xml_data = urllib.request.urlopen(req).read().decode("utf-8")
    root = ET.fromstring(xml_data)

    def _get_highest_ver(array_name: str) -> str:
        items = []
        for sa in root.findall("string-array"):
            if sa.get("name") == array_name:
                items = [item.text for item in sa.findall("item") if item.text]
        return items[-1] if items else "latest"

    wpp_ver = _get_highest_ver("supported_versions_wpp")
    biz_ver = _get_highest_ver("supported_versions_business")

    return wpp_ver, biz_ver


def _to_wildcard(ver: str) -> str:
    """Convert an exact 4-part version to a wildcard patch version (e.g. 2.26.30.85 -> 2.26.30.xx)."""
    parts = ver.strip().split(".")
    if len(parts) >= 4:
        parts[-1] = "xx"
        return ".".join(parts)
    return ver


def _patch_version_line(content: str, table: str, new_ver: str) -> tuple[str, bool]:
    """Replace the `version` line inside the [table] block, preserving every other key."""
    block_re = re.compile(rf"^\[{re.escape(table)}\](?:\r?\n(?!\[).*)*", re.MULTILINE)
    m = block_re.search(content)
    if not m:
        return content, False

    block = m.group(0)
    new_block, count = re.subn(r'(?m)^(\s*version\s*=\s*).*$', rf'\1"{new_ver}"', block, count=1)
    if not count:
        return content, False

    return content[: m.start()] + new_block + content[m.end() :], True


def update_config_toml() -> None:
    """Fetch WaEnhancer recommended versions and update the WhatsApp version lines in config.toml."""
    wpp_ver, biz_ver = fetch_recommended_wa_versions()

    if not CONFIG_PATH.exists():
        print("[-] config.toml not found", file=sys.stderr)
        return

    content = CONFIG_PATH.read_text(encoding="utf-8")
    new_content = content
    changed = False
    for table, ver in (("WhatsApp", _to_wildcard(wpp_ver)), ("WhatsApp-Business", _to_wildcard(biz_ver))):
        new_content, updated = _patch_version_line(new_content, table, ver)
        if updated:
            print(f"[+] Updated [{table}] version to '{ver}' in config.toml")
            changed = True
        else:
            print(f"[!] Could not find [{table}] or its 'version' key in config.toml, skipping")

    if changed and new_content != content:
        CONFIG_PATH.write_text(new_content, encoding="utf-8")


if __name__ == "__main__":
    update_config_toml()
