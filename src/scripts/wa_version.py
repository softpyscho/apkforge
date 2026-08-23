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

from src.core.logger import pr

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


def update_config_toml() -> bool:
    """Fetch WaEnhancer recommended versions and update WhatsApp sections in config.toml."""
    wpp_ver, biz_ver = fetch_recommended_wa_versions()

    if not CONFIG_PATH.exists():
        print("[-] config.toml not found", file=sys.stderr)
        return False

    content = CONFIG_PATH.read_text(encoding="utf-8")
    original_content = content

    # WhatsApp entry
    wa_entry = (
        f"[WhatsApp]\n"
        f'app-name = "WhatsApp"\n'
        f"mirror = true\n"
        f'badge-color = "25D366"\n'
        f'badge-icon = "whatsapp"\n'
        f'version = "{wpp_ver}"\n'
        f'arch = "arm64-v8a"\n'
        f'pkg-name = "com.whatsapp"\n'
        f'apkmirror-dlurl = "https://www.apkmirror.com/apk/whatsapp-inc/whatsapp/"\n'
        f'uptodown-dlurl = "https://whatsapp-messenger.en.uptodown.com/android"\n'
        f'apkpure-dlurl = "https://apkpure.com/whatsapp-messenger/com.whatsapp"'
    )

    # WhatsApp Business entry
    wa_biz_entry = (
        f"[WhatsApp-Business]\n"
        f'app-name = "WhatsApp Business"\n'
        f"mirror = true\n"
        f'badge-color = "25D366"\n'
        f'badge-icon = "whatsapp"\n'
        f'version = "{biz_ver}"\n'
        f'arch = "arm64-v8a"\n'
        f'pkg-name = "com.whatsapp.w4b"\n'
        f'apkmirror-dlurl = "https://www.apkmirror.com/apk/whatsapp-inc/whatsapp-business/"\n'
        f'uptodown-dlurl = "https://whatsapp-business.en.uptodown.com/android"\n'
        f'apkpure-dlurl = "https://apkpure.com/whatsapp-business/com.whatsapp.w4b"'
    )

    if "[WhatsApp]" in content:
        content = re.sub(r"\[WhatsApp\].*?(?=\n\[|\Z)", wa_entry, content, flags=re.DOTALL)
    else:
        content = content.rstrip() + f"\n\n{wa_entry}\n"

    if "[WhatsApp-Business]" in content:
        content = re.sub(r"\[WhatsApp-Business\].*?(?=\n\[|\Z)", wa_biz_entry, content, flags=re.DOTALL)
    else:
        content = content.rstrip() + f"\n\n{wa_biz_entry}\n"

    if content != original_content:
        CONFIG_PATH.write_text(content, encoding="utf-8")
        print("[+] Successfully updated config.toml with WhatsApp and WhatsApp Business entries.")
        return True
    return False


if __name__ == "__main__":
    update_config_toml()
