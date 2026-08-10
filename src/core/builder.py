# ---------------------------------------------------------
# Copyright (C) 2026 softpyscho
# 
# DO NOT REMOVE OR ALTER THIS COPYRIGHT HEADER.
# This file is part of apkforge.
# Licensed under the GNU GPLv3. You may modify this file,
# but you MUST keep this original copyright notice intact
# and prominently state any changes made.
# See the AUTHORS file in the root directory for details.
# ---------------------------------------------------------

import io
import struct
import base64
import os
import re
import shutil
import tempfile
import zipfile
import subprocess
import sys
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from pathlib import Path
import json

from src.core.config import BUILD_DIR, TEMP_DIR, ORIGINAL_APK_DIR, AppEntry, Config
from src.core.logger import IS_GITHUB, epr, is_interrupted, pr, wpr
from src.core.network import NetworkError, NetworkManager
from src.core.patcher import PatcherCLI, PatcherError, SignatureError
from src.core.prebuilts import APKSIGNER, fetch_cli, fetch_mpp, get_highest_ver
from src.scrapers.base import BaseScraper, DownloadResult, ScraperError

_failed_signatures: set[str] = set()
_patches_info: dict[str, list[str]] = {}
try:
    if Path("patches_info.json").exists():
        _patches_info = json.loads(Path("patches_info.json").read_text(encoding="utf-8"))
except Exception:
    pass


def parse_axml_strings(axml_data: bytes) -> list[str]:
    """Parse string pool from binary AndroidManifest.xml (AXML)."""
    strings = []
    try:
        if len(axml_data) < 32:
            return strings
        idx = axml_data.find(b"\x01\x00\x1c\x00")
        if idx == -1:
            return strings

        string_count = struct.unpack("<I", axml_data[idx + 8 : idx + 12])[0]
        flags = struct.unpack("<I", axml_data[idx + 16 : idx + 20])[0]
        strings_start = idx + struct.unpack("<I", axml_data[idx + 20 : idx + 24])[0]
        is_utf8 = (flags & (1 << 8)) != 0

        offsets = []
        off_pos = idx + 28
        for _ in range(min(string_count, 5000)):
            offsets.append(struct.unpack("<I", axml_data[off_pos : off_pos + 4])[0])
            off_pos += 4

        for off in offsets:
            pos = strings_start + off
            if is_utf8:
                if pos >= len(axml_data):
                    continue
                u8len = axml_data[pos]
                if u8len & 0x80:
                    pos += 2
                else:
                    pos += 1
                end = axml_data.find(b"\x00", pos)
                if end != -1:
                    strings.append(axml_data[pos:end].decode("utf-8", errors="ignore"))
            else:
                if pos + 2 > len(axml_data):
                    continue
                u16len = struct.unpack("<H", axml_data[pos : pos + 2])[0]
                pos += 2
                strings.append(axml_data[pos : pos + u16len * 2].decode("utf-16le", errors="ignore"))
    except Exception:
        pass
    return strings


def extract_apk_version(apk_path: Path) -> str | None:
    """Extract actual versionName string from AndroidManifest.xml inside APK or bundle."""
    ver_regex = re.compile(r"^\d+\.\d+(?:\.\d+)+(?:-[a-zA-Z0-9.]+)?$")

    def _find_ver(axml: bytes) -> str | None:
        strs = parse_axml_strings(axml)
        for s in strs:
            s_clean = s.strip()
            if ver_regex.match(s_clean) and not s_clean.startswith(("7.1.", "8.0.", "9.0.")):
                return s_clean
        for s in strs:
            s_clean = s.strip()
            if re.search(r"^\d+\.\d+\.\d+", s_clean):
                return s_clean
        return None

    try:
        if apk_path.suffix == ".apk":
            with zipfile.ZipFile(apk_path, "r") as zf:
                if "AndroidManifest.xml" in zf.namelist():
                    return _find_ver(zf.read("AndroidManifest.xml"))
        elif apk_path.suffix in (".apkm", ".xapk"):
            with zipfile.ZipFile(apk_path, "r") as zf:
                for name in zf.namelist():
                    if name.endswith(".apk") and not name.startswith("config."):
                        inner_bytes = zf.read(name)
                        with zipfile.ZipFile(io.BytesIO(inner_bytes), "r") as inner_zf:
                            if "AndroidManifest.xml" in inner_zf.namelist():
                                v = _find_ver(inner_zf.read("AndroidManifest.xml"))
                                if v:
                                    return v
    except Exception:
        pass
    return None


def _parse_patch_names(list_patches_output: str) -> list[str]:
    """Extract default patch names from the patcher's list-patches output."""
    default_patches = []
    # Split by empty lines or INFO: to isolate each patch block
    blocks = re.split(r'\n\s*\n|\nINFO:', list_patches_output)
    for block in blocks:
        name_match = re.search(r"^\s*Name:\s*(.+)$", block, re.MULTILINE)
        if name_match:
            name = name_match.group(1).strip()
            # Check if this patch is enabled by default
            default_match = re.search(r"^\s*Default:\s*(true|false)", block, re.IGNORECASE | re.MULTILINE)
            if default_match and default_match.group(1).lower() == "true":
                default_patches.append(name)
                
    # Fallback to all patches if parsing Default fails
    if not default_patches:
        return [m.group(1).strip() for m in re.finditer(r"^\s*Name:\s*(.+)$", list_patches_output, re.MULTILINE)]
    return default_patches


class BuilderError(Exception):
    pass


def _parse_ver(v_str: str) -> tuple:
    """Safely parse a version string into a comparable tuple key."""
    cleaned = re.sub(r"^[vV]", "", v_str.strip())
    parts = []
    for token in re.split(r"[._-]", cleaned):
        if token.isdigit():
            parts.append((0, int(token), ""))
        else:
            m = re.match(r"^(\d+)(.*)$", token)
            if m:
                parts.append((0, int(m.group(1)), m.group(2)))
            else:
                parts.append((1, 0, token))
    return tuple(parts)


def _get_versions_below(versions: list[str], target_ver: str) -> list[str]:
    """Return versions strictly below target_ver, sorted from highest to lowest."""
    target_key = _parse_ver(target_ver)
    valid = []
    for v in versions:
        try:
            if _parse_ver(v) < target_key:
                valid.append(v)
        except Exception:
            continue
    valid.sort(key=_parse_ver, reverse=True)
    return valid

_APK_MIN_SIZE = 1_000_000  # 1MB — no real APK is smaller
_ZIP_MAGIC = b'PK\x03\x04'

def _validate_download(path: Path) -> None:
    """Reject downloads that are obviously not APKs."""
    size = path.stat().st_size
    if size < _APK_MIN_SIZE:
        path.unlink(missing_ok=True)
        raise BuilderError(f"Download too small ({size} bytes) — likely an error page")
    
    with path.open('rb') as f:
        magic = f.read(4)
    if magic != _ZIP_MAGIC:
        path.unlink(missing_ok=True)
        raise BuilderError("Download is not a valid APK/ZIP file")


def _make_scraper(source: str, net: NetworkManager) -> BaseScraper:
    from src.scrapers.apkmirror import APKMirrorScraper
    from src.scrapers.apkpure import APKPureScraper
    from src.scrapers.github import GitHubScraper
    from src.scrapers.uptodown import UptodownScraper
    match source:
        case "apkmirror":
            return APKMirrorScraper(net)
        case "github":
            return GitHubScraper(net)
        case "uptodown":
            return UptodownScraper(net)
        case "apkpure":
            return APKPureScraper(net)
        case "direct":
            from src.scrapers.direct import DirectScraper
            return DirectScraper(net)
        case _:
            raise ValueError(f"Unknown APK source: {source!r}")


def _find_pkg_name(entry: AppEntry, scrapers: dict[str, BaseScraper]) -> tuple[str, str, set[str]]:
    failed: set[str] = set()
    
    known_pkgs = {
        "instagram": "com.instagram.android",
        "twitter": "com.twitter.android",
        "x": "com.twitter.android",
        "reddit": "com.reddit.frontpage",
        "youtube": "com.google.android.youtube",
        "youtube-music": "com.google.android.apps.youtube.music",
        "tiktok": "com.ss.android.ugc.trill",
    }

    for src, url in entry.dl_urls.items():
        try:
            metadata = scrapers[src].cached_metadata(url)
            pkg_name = getattr(entry, "pkg_name", None) or metadata.pkg_name
            
            if pkg_name and pkg_name.lower() in known_pkgs:
                pkg_name = known_pkgs[pkg_name.lower()]

            pr(f"Package name of '{entry.table}' is '{pkg_name}'")
            return pkg_name, src, failed
        except (NetworkError, ScraperError) as exc:
            epr(f"Could not find '{entry.table}' in '{src}': {exc}")
            failed.add(src)

    if entry.pkg_name:
        first_src = next(iter(entry.dl_urls.keys()))
        pr(f"Package name of '{entry.table}' is '{entry.pkg_name}' (from config)")
        return entry.pkg_name, first_src, failed

    raise BuilderError("Package name not found")


def _resolve_version(entry: AppEntry, patcher: PatcherCLI | None, list_patches: str, pkg_name: str, dl_from: str, scrapers: dict[str, BaseScraper]) -> tuple[str, bool]:
    is_wildcard = entry.version.endswith(".xx")
    prefix = entry.version[:-3] if is_wildcard else ""

    if entry.version not in ("auto", "latest") and not is_wildcard:
        version, is_custom = entry.version, True
    elif entry.version in ("auto", "latest") and patcher and (v := patcher.get_last_supported_version(list_patches, pkg_name, entry.patches, experimental=entry.version == "latest")):
        version, is_custom = v, False
    else:
        version = ""
        sources_to_try = [dl_from] + [s for s in entry.dl_urls if s != dl_from]
        for src in sources_to_try:
            try:
                versions = scrapers[src].cached_metadata(entry.dl_urls[src]).versions
                if is_wildcard:
                    matching = [v for v in versions if v.startswith(f"{prefix}.")]
                    version = get_highest_ver(matching) if matching else (get_highest_ver(versions) if versions else "")
                else:
                    version = get_highest_ver(versions) if versions else ""
                if version:
                    break
            except (NetworkError, ScraperError):
                continue

        if not version:
            cached_vers = []
            for cached_file in ORIGINAL_APK_DIR.iterdir():
                if cached_file.is_file() and cached_file.name.startswith(f"{pkg_name}-v") and cached_file.name.endswith((".apk", ".apkm", ".xapk")):
                    m_ver = re.search(r"-v([^-]+)-", cached_file.name)
                    if m_ver:
                        c_ver = m_ver.group(1)
                        if not is_wildcard or c_ver.startswith(f"{prefix}."):
                            cached_vers.append(c_ver)
            if cached_vers:
                version = get_highest_ver(cached_vers)
                pr(f"Found cached version '{version}' for '{entry.table}' in '{ORIGINAL_APK_DIR}'")
            elif pkg_name:
                for cached_file in ORIGINAL_APK_DIR.iterdir():
                    if cached_file.is_file() and cached_file.name.startswith(f"{pkg_name}-v") and cached_file.name.endswith((".apk", ".apkm", ".xapk")):
                        m_ver = re.search(r"-v([^-]+)-", cached_file.name)
                        if m_ver:
                            cached_vers.append(m_ver.group(1))
                if cached_vers:
                    version = get_highest_ver(cached_vers)
                    pr(f"Found fallback cached version '{version}' for '{entry.table}' in '{ORIGINAL_APK_DIR}'")

        if not version:
            if is_wildcard:
                version = f"{prefix}.0"
            else:
                version = "latest"
        is_custom = entry.version not in ("auto", "latest")

    pr(f"Choosing version '{version}' for '{entry.table}'")
    return version, is_custom


def _download_apk(entry: AppEntry, version: str, arch: str, pkg_name: str, scrapers: dict[str, BaseScraper], dl_from: str, failed_sources: set[str]) -> DownloadResult:
    arch_f = arch.replace(" ", "")
    version_f = version.replace(" ", "").lstrip("v")
    base_name = f"{pkg_name}-v{version_f}-{arch_f}.apk"
    stock_apk = ORIGINAL_APK_DIR / base_name

    def _read_src(path: Path) -> str:
        src_meta = path.with_suffix(".src")
        if src_meta.exists():
            return src_meta.read_text(encoding="utf-8").strip()
        return dl_from

    if stock_apk.exists():
        pr(f"Reusing existing cached APK: {stock_apk.name}")
        orig_name = ""
        orig_meta = stock_apk.with_suffix(".orig")
        if orig_meta.exists():
            orig_name = orig_meta.read_text(encoding="utf-8").strip()
        return DownloadResult(path=stock_apk, is_bundle=False, original_name=orig_name, source_used=_read_src(stock_apk))

    stock_apkm = stock_apk.with_suffix(".apkm")
    if stock_apkm.exists():
        pr(f"Reusing existing cached APK: {stock_apkm.name}")
        orig_name = ""
        orig_meta = stock_apkm.with_suffix(".orig")
        if orig_meta.exists():
            orig_name = orig_meta.read_text(encoding="utf-8").strip()
        return DownloadResult(path=stock_apkm, is_bundle=True, original_name=orig_name, source_used=_read_src(stock_apkm))

    # Cleanup old versions of this specific package before downloading the new one
    for old_file in ORIGINAL_APK_DIR.iterdir():
        if old_file.is_file() and old_file.name.startswith(f"{pkg_name}-v") and old_file.name.endswith((".apk", ".apkm", ".xapk", ".orig", ".src")):
            if f"-v{version_f}-" not in old_file.name:  # Don't delete other architectures of the current version
                pr(f"Deleting outdated APK version: {old_file.name}")
                old_file.unlink(missing_ok=True)

    ordered_sources = [s for s in entry.dl_urls if s not in failed_sources]
    if dl_from in ordered_sources:
        ordered_sources.remove(dl_from)
        ordered_sources.insert(0, dl_from)
    if not ordered_sources:
        ordered_sources = [dl_from] + [s for s in entry.dl_urls if s != dl_from]
        seen = set()
        ordered_sources = [s for s in ordered_sources if not (s in seen or seen.add(s))]

    for src in ordered_sources:
        url = entry.dl_urls[src]
        pr(f"Downloading '{entry.table}' from '{src}'")
        try:
            res = scrapers[src].download(url, version, stock_apk, arch, entry.dpi)
            _validate_download(res.path)
            if res.original_name:
                res.path.with_suffix(".orig").write_text(res.original_name, encoding="utf-8")
            res.path.with_suffix(".src").write_text(src, encoding="utf-8")
            return DownloadResult(path=res.path, is_bundle=res.is_bundle, original_name=res.original_name, source_used=src)
        except (NetworkError, ScraperError, BuilderError) as exc:
            epr(f"Failed to fetch '{entry.table}' from '{src}' (version='{version}', arch='{arch}'): {exc}")
    raise BuilderError("Stock APK not found")


def _optimize_bundle(src_bundle: Path, dest_bundle: Path, target_arch: str) -> None:
    """
    Reads an .apkm or .xapk bundle and writes a new one stripping out all unused
    languages (keeps only English), architectures, and densities (keeps only xxhdpi).
    """
    pr(f"Optimizing split bundle: Extracting base + English + xxhdpi + {target_arch}...")
    
    # Regex for standard split structures
    re_lang = re.compile(r'(?:split_)?config\.([a-z]{2}(?:-[a-zA-Z]{2,3})?)\.apk', re.IGNORECASE)
    re_dpi = re.compile(r'(?:split_)?config\.(l|m|tv|h|xh|xxh|xxxh)dpi\.apk', re.IGNORECASE)
    re_abi = re.compile(r'(?:split_)?config\.(armeabi_v7a|arm64_v8a|x86|x86_64)\.apk', re.IGNORECASE)
    
    target_abi = "arm64_v8a" if "arm64" in target_arch.lower() else "armeabi_v7a"
    
    with zipfile.ZipFile(src_bundle, 'r') as z_in, zipfile.ZipFile(dest_bundle, 'w') as z_out:
        for item in z_in.infolist():
            lower_name = item.filename.lower()
            
            # Non-APK files (like metadata) are kept to preserve bundle structure
            if not lower_name.endswith('.apk'):
                z_out.writestr(item, z_in.read(item.filename))
                continue
                
            keep = True
            
            # Check if it's a language split we don't want
            lang_match = re_lang.search(lower_name)
            if lang_match:
                lang = lang_match.group(1).lower()
                if not lang.startswith('en'):
                    keep = False
            
            # Check if it's a DPI split we don't want
            dpi_match = re_dpi.search(lower_name)
            if dpi_match:
                dpi = dpi_match.group(1).lower()
                if dpi != 'xxh':
                    keep = False
                    
            # Check if it's an architecture split we don't want
            abi_match = re_abi.search(lower_name)
            if abi_match:
                abi = abi_match.group(1).lower()
                if abi != target_abi:
                    keep = False
                    
            if keep:
                z_out.writestr(item, z_in.read(item.filename))


def _extract_base_apk(apkm: Path, pkg_name: str, dest_dir: Path) -> Path:
    with zipfile.ZipFile(apkm, "r") as zf:
        names = zf.NameToInfo
        for name in ("base.apk", f"{pkg_name}.apk"):
            if name in names:
                zf.extract(name, dest_dir)
                return dest_dir / name
    raise BuilderError(f"Neither 'base.apk' nor '{pkg_name}.apk' found inside {apkm.name}")


def _verify_sig(dl_result: DownloadResult, pkg_name: str, patcher: PatcherCLI, table: str, skip_sigcheck: bool, strict_sigcheck: bool) -> None:
    if skip_sigcheck:
        wpr(f"Skipping APK signature verification for '{table}'")
        return

    if not patcher.has_signature(pkg_name):
        msg = f"No signature entry found in sig.txt for '{pkg_name}'"
        if strict_sigcheck:
            raise SignatureError(msg)

        wpr(f"{msg}, skipping it")
        return

    if not dl_result.is_bundle:
        if not patcher.check_signature(dl_result.path, pkg_name):
            raise SignatureError("APK signature mismatch")
        return

    with tempfile.TemporaryDirectory(dir=TEMP_DIR) as tmp_dir:
        apk_path = _extract_base_apk(dl_result.path, pkg_name, Path(tmp_dir))
        if not patcher.check_signature(apk_path, pkg_name):
            raise SignatureError("Bundle APK signature mismatch")


def _apply_patch(entry: AppEntry, arch: str, version: str, force: bool, patcher: PatcherCLI, list_patches: str, dl_result: DownloadResult, excluded_patches: list[str]) -> Path:
    arch_f = arch.replace(" ", "")
    version_f = version.replace(" ", "").lstrip("v")
    auto_patches = patcher.resolve_auto_patches(list_patches)
    
    dynamic_args = list(entry.patcher_args)
    for p in excluded_patches:
        dynamic_args.extend(["-e", p])
        
    final_args = patcher.build_patch_args(patches=entry.patches, extra_args=dynamic_args, arch=arch, auto_patches=auto_patches, exclusive=entry.exclusive_patches, force=force)
    base_name = f"{entry.app_name.lower().replace(' ', '-')}-{entry.brand.lower().replace(' ', '-')}"
    apk_name = f"{base_name}-v{version_f}-{arch_f}.apk"
    patched_apk = TEMP_DIR / apk_name

    pr(f"Building '{entry.table}'")

    captured_out = []
    
    def hooked_run(*args, **kwargs):
        kwargs['capture_output'] = True
        kwargs['text'] = True
        res = subprocess.run(*args, **kwargs)
        if res.stdout:
            print(res.stdout)
            captured_out.append(res.stdout)
        if res.stderr:
            print(res.stderr, file=sys.stderr)
            captured_out.append(res.stderr)
        return res
        
    try:
        patcher.patch(dl_result.path, patched_apk, final_args, run_fn=hooked_run)
    except Exception as exc:
        full_out = "\n".join(captured_out)
        raise BuilderError(f"{exc}\n{full_out}") from exc

    apk_output = BUILD_DIR / apk_name
    shutil.move(patched_apk, apk_output)
    return apk_output


def _build_single(entry: AppEntry, arch: str, label: str, net: NetworkManager, patcher: PatcherCLI | None, strict_sigcheck: bool) -> dict | None:
    if entry.table in _failed_signatures:
        epr(f"Skipped '{label}' due to previous signature mismatch")
        return None

    try:
        scrapers = {src: _make_scraper(src, net) for src in entry.dl_urls}
        pkg_name, dl_from, failed_sources = _find_pkg_name(entry, scrapers)
        list_patches = ""
        if patcher:
            list_patches = patcher.list_patches(pkg_name, experimental=entry.version == "latest")
            _patches_info[entry.table] = _parse_patch_names(list_patches)
        version, force = _resolve_version(entry, patcher, list_patches, pkg_name, dl_from, scrapers)

        try:
            dl_result = _download_apk(entry, version, arch, pkg_name, scrapers, dl_from, failed_sources)
        except BuilderError as exc:
            cached_candidates = []
            if pkg_name:
                for cached_file in ORIGINAL_APK_DIR.iterdir():
                    if cached_file.is_file() and cached_file.name.startswith(f"{pkg_name}-v") and cached_file.name.endswith((".apk", ".apkm", ".xapk")):
                        m_ver = re.search(r"-v([^-]+)-", cached_file.name)
                        if m_ver:
                            cached_candidates.append(m_ver.group(1))

            if cached_candidates:
                fallback_ver = get_highest_ver(cached_candidates)
                wpr(f"Online download failed for '{entry.table}'. Reusing cached version '{fallback_ver}' from '{ORIGINAL_APK_DIR}'...")
                dl_result = _download_apk(entry, fallback_ver, arch, pkg_name, scrapers, dl_from, failed_sources)
                version = fallback_ver
                force = True
            elif entry.version in ("auto", "latest"):
                fallback_version = None
                dl_result_fallback = None
                fallback_order = [s for s in ["uptodown", "apkpure", "github", "apkmirror"] if s in entry.dl_urls]

                for src in fallback_order:
                    if src in failed_sources: continue
                    try:
                        versions = scrapers[src].cached_metadata(entry.dl_urls[src]).versions
                        if not versions: continue
                        lower_candidates = _get_versions_below(versions, version)
                        for candidate_ver in lower_candidates:
                            wpr(f"Target '{version}' unavailable. Trying lower version '{candidate_ver}' from '{src}'...")
                            try:
                                dl_result_fallback = _download_apk(entry, candidate_ver, arch, pkg_name, scrapers, src, failed_sources)
                                fallback_version = candidate_ver
                                break
                            except BuilderError: continue
                        if dl_result_fallback: break
                    except Exception:
                        failed_sources.add(src)

                if not dl_result_fallback:
                    for src in fallback_order:
                        if src in failed_sources: continue
                        try:
                            versions = scrapers[src].cached_metadata(entry.dl_urls[src]).versions
                            if not versions: continue
                            highest_ver = get_highest_ver(versions)
                            if highest_ver:
                                wpr(f"No lower versions available. Falling back to highest available '{highest_ver}' from '{src}'...")
                                try:
                                    dl_result_fallback = _download_apk(entry, highest_ver, arch, pkg_name, scrapers, src, failed_sources)
                                    fallback_version = highest_ver
                                    break
                                except BuilderError: continue
                        except Exception:
                            failed_sources.add(src)

                if fallback_version and dl_result_fallback:
                    version = fallback_version
                    force = True
                    if patcher:
                        list_patches = patcher.list_patches(pkg_name, experimental=True)
                    dl_result = dl_result_fallback
                else:
                    raise exc
            else:
                raise exc

        # Extract actual versionName from APK manifest if version is unspecific ("latest", "auto", "nightly", etc.)
        real_ver = extract_apk_version(dl_result.path)
        if real_ver:
            if version in ("latest", "auto", "nightly") or not version or not re.search(r"\d+\.\d+", version):
                pr(f"Extracted actual version '{real_ver}' from APK manifest for '{entry.table}' (was '{version}')")
                version = real_ver
                force = True

        if patcher:
            _verify_sig(dl_result, pkg_name, patcher, label, entry.skip_sigcheck, strict_sigcheck)
        
        # ----------------------------------------------------
        # NEW: Bundle Optimization logic (Strip bloat splits)
        # ----------------------------------------------------
        if dl_result.is_bundle:
            optimized_bundle = TEMP_DIR / f"lean_{dl_result.path.name}"
            _optimize_bundle(dl_result.path, optimized_bundle, arch)
            
            # Point the dl_result to the new lightweight bundle for the patcher
            dl_result = DownloadResult(path=optimized_bundle, is_bundle=True, source_used=dl_result.source_used)
            
        
        if entry.mirror:
            if entry.keep_filename and dl_result.original_name:
                apk_name = dl_result.original_name
            else:
                arch_f = arch.replace(" ", "")
                version_f = version.replace(" ", "").lstrip("v")
                base_name = f"{entry.app_name.lower().replace(' ', '-')}-mirror"
                apk_name = f"{base_name}-v{version_f}-{arch_f}.apk"
            apk_output = BUILD_DIR / apk_name
            shutil.copy(dl_result.path, apk_output)
            excluded_patches = []
            
            pr(f"Mirrored {label}: '{apk_output}'")
            github_asset_name = re.sub(r"\.+", ".", re.sub(r"[^a-zA-Z0-9@+\-_.]", ".", apk_output.name))
            ver_str = f"[`{version}`](https://github.com/{os.getenv('GITHUB_REPOSITORY')}/releases/download/{{TAG}}/{github_asset_name})" if IS_GITHUB else f"`{version}`"
            
            return {"app": entry.table, "label": label, "version": version, "apk": apk_output.name, "source": dl_result.source_used, "excluded_patches": [], "success": True, "log": f"- 🟢 » {label}: {ver_str} (Mirrored)"}

        # Dynamic Exclude Loop (Max 5 retries to prevent endless loops)
        excluded_patches = []
        max_retries = 5
        apk_output = None
        
        for attempt in range(max_retries):
            try:
                apk_output = _apply_patch(entry, arch, version, force, patcher, list_patches, dl_result, excluded_patches)
                break
            except (PatcherError, BuilderError) as exc:
                clean_exc = re.sub(r'\x1b\[[0-9;]*m', '', str(exc))
                match = re.search(r"FAILED:\s*([^\r\n]+)", clean_exc)
                
                if match:
                    failed_patch = match.group(1).strip()
                    if failed_patch in excluded_patches:
                        raise BuilderError(f"Patch '{failed_patch}' failed again after being excluded.")
                        
                    wpr(f"Patch '{failed_patch}' failed. Excluding and retrying ({attempt + 1}/{max_retries})...")
                    excluded_patches.append(failed_patch)
                else:
                    raise  
        else:
            raise BuilderError(f"Failed to patch '{label}' after {max_retries} attempts.")

        pr(f"Built {label}: '{apk_output}'")
        github_asset_name = re.sub(r"\.+", ".", re.sub(r"[^a-zA-Z0-9@+\-_.]", ".", apk_output.name))
        ver_str = f"[`{version}`](https://github.com/{os.getenv('GITHUB_REPOSITORY')}/releases/download/{{TAG}}/{github_asset_name})" if IS_GITHUB else f"`{version}`"
        
        excluded_str = ", ".join(excluded_patches) if excluded_patches else ""
        return {"app": entry.table, "label": label, "version": version, "apk": apk_output.name, "source": dl_result.source_used, "excluded_patches": excluded_patches, "success": True, "log": f"- 🟢 » {label}: {ver_str}" + (f" <br> ⚠️ *(Excluded due to build errors: {excluded_str})*" if excluded_patches else "")}
    except (BuilderError, PatcherError, ScraperError, NetworkError, SignatureError) as exc:
        if isinstance(exc, SignatureError):
            _failed_signatures.add(entry.table)

        if not is_interrupted():
            epr(f"Building '{label}' failed! {exc}")
        return {"app": entry.table, "label": label, "success": False, "error": str(exc), "log": None}


def _submit_entries(entries: list[AppEntry], pool: ThreadPoolExecutor, net: NetworkManager, ks_path: Path | None, strict_sigcheck: bool) -> list[Future[dict | None]]:
    futures: list[Future[dict | None]] = []
    cli_cache: dict[tuple[str, str], Path] = {}
    for e in entries:
        if not e.dl_urls or e.mirror:
            continue

        key = (e.cli_source, e.cli_version)
        if key not in cli_cache:
            try:
                cli_cache[key] = fetch_cli(e.cli_source, e.cli_version, net)
            except Exception as exc:
                epr(f"Could not fetch CLI '{e.cli_source}': {exc}")

    all_patch_srcs = {(src, spec["version"]) for e in entries if e.dl_urls and not e.mirror for src, spec in e.patches.items()}
    mpp_map: dict[tuple[str, str], Path] = {}
    for src, ver in all_patch_srcs:
        try:
            mpp_map[(src, ver)] = fetch_mpp(src, ver, net)
        except Exception as exc:
            epr(f"Could not fetch patches from '{src}': {exc}")

    for entry in entries:
        if not entry.dl_urls:
            epr(f"No 'dlurl' option was set for '{entry.table}'")
            continue
        if not entry.patches and not entry.mirror:
            epr(f"No 'patches' table defined for '{entry.table}'")
            continue

        patcher = None
        if not entry.mirror:
            cli_key = (entry.cli_source, entry.cli_version)
            if cli_key not in cli_cache:
                continue

            app_mpp_map = {(src, spec["version"]): mpp_map[(src, spec["version"])] for src, spec in entry.patches.items() if (src, spec["version"]) in mpp_map}
            if not app_mpp_map:
                epr(f"No patch files available for '{entry.table}'")
                continue

            patcher = PatcherCLI(cli_cache[cli_key], app_mpp_map, APKSIGNER, ks_path=ks_path)
            
        arches = ("arm64-v8a", "armeabi-v7a") if entry.arch == "both" else (entry.arch,)
        for arch in arches:
            label = entry.app_name if entry.arch == "all" else f"{entry.app_name} ({arch})"
            futures.append(pool.submit(_build_single, entry, arch, label, net, patcher, strict_sigcheck))
    return futures


def run_build(entries: list[AppEntry], config: Config, net: NetworkManager) -> bool:
    if not entries:
        epr("No entries to build")
        return False

    ks_path: Path | None = None
    if ks_b64 := os.getenv("KEYSTORE_BASE64"):
        with tempfile.NamedTemporaryFile(dir=TEMP_DIR, suffix=".keystore", delete=False) as tf:
            tf.write(base64.b64decode(ks_b64))
            ks_path = Path(tf.name)

    try:
        with ThreadPoolExecutor(max_workers=config.parallel_jobs) as pool:
            futures = _submit_entries(entries, pool, net, ks_path, config.strict_sigcheck)
    finally:
        if ks_path:
            ks_path.unlink(missing_ok=True)

    for tmp in TEMP_DIR.rglob("tmp*"):
        shutil.rmtree(tmp, ignore_errors=True)

    log_lines: list[str] = []
    report_data = {"success": [], "failed": [], "excluded_patches": {}}
    for fut in as_completed(futures):
        if r := fut.result():
            if r["success"]:
                log_lines.append(r["log"])
                report_data["success"].append({"app": r["app"], "label": r["label"], "version": r["version"], "apk": r["apk"], "source": r["source"]})
                if r["excluded_patches"]:
                    report_data["excluded_patches"][r["label"]] = r["excluded_patches"]
            else:
                report_data["failed"].append({"app": r["app"], "label": r["label"], "error": r["error"]})

    Path("build.json").write_text(json.dumps(report_data, indent=2))
    Path("patches_info.json").write_text(json.dumps(_patches_info, indent=2))

    if not log_lines:
        epr("All builds failed")
        return False

    raw = "".join(cl.read_text(encoding="utf-8") for cl in sorted(TEMP_DIR.glob("*/changelog.md")))
    block_re = re.compile(r"^> ⚙️ » (CLI|Patches):.*?(?=^> ⚙️ »|\Z)", re.MULTILINE | re.DOTALL)
    cli_blocks: list[str] = []
    patch_blocks: list[str] = []
    for m in block_re.finditer(raw):
        (cli_blocks if m.group(1) == "CLI" else patch_blocks).append(m.group())
    changelogs = "".join(cli_blocks) + "".join(patch_blocks)
    microg_line = "▶️ » Install [MicroG-RE](https://github.com/MorpheApp/MicroG-RE/releases) to enable Google account sign-in for supported apps\n"
    Path("build.md").write_text("\n".join([*log_lines, "", microg_line, changelogs]), encoding="utf-8")
    pr("Done")
    return True
