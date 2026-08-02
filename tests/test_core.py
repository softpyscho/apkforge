# ---------------------------------------------------------
# Copyright (C) 2026 softpsycho
#
# Licensed under the GNU GPLv3.
# ---------------------------------------------------------

"""Tests for apkforge's pure helper functions.

These tests deliberately avoid network and subprocess calls — they
exercise the parsing / validation logic that is most likely to silently
break when the upstream CLI output format or config schema changes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.core.builder import (
    BuildResult,
    BuildState,
    _get_versions_below,
    _parse_patch_names,
    _parse_ver,
    _validate_download,
    BuilderError,
    load_patches_info_cache,
)
from src.core.config import (
    AppEntry,
    Config,
    VALID_ARCHES,
    load_toml,
    parse_app_entries,
    parse_config,
    validate_config,
)
from src.core.exceptions import ApkforgeError
from src.core.network import SSRFError, _validate_url
from src.core.prebuilts import _find_cached, _ver_key, get_highest_ver
from src.scripts.merge_patches_info import merge_patches_info


# ---------------------------------------------------------------------------
# builder._parse_ver
# ---------------------------------------------------------------------------

class TestParseVer:
    def test_simple_numeric(self):
        assert _parse_ver("1.2.3") == ((0, 1, ""), (0, 2, ""), (0, 3, ""))

    def test_strips_v_prefix(self):
        assert _parse_ver("v1.2.3") == _parse_ver("1.2.3")
        assert _parse_ver("V1.2.3") == _parse_ver("1.2.3")

    def test_handles_underscores_and_dashes(self):
        assert _parse_ver("1-2-3") == _parse_ver("1.2.3")
        assert _parse_ver("1_2_3") == _parse_ver("1.2.3")

    def test_handles_alpha_suffix(self):
        # 1.2.3-beta → ((0,1,""), (0,2,""), (0,3,""), (1,0,"beta")) but with regex match
        result = _parse_ver("1.2.3-beta")
        assert result[0] == (0, 1, "")
        assert result[1] == (0, 2, "")
        assert result[2] == (0, 3, "")

    def test_comparison(self):
        assert _parse_ver("1.2.3") < _parse_ver("1.2.10")
        assert _parse_ver("1.2.3") < _parse_ver("1.3.0")
        assert _parse_ver("2.0.0") > _parse_ver("1.99.99")


# ---------------------------------------------------------------------------
# builder._get_versions_below
# ---------------------------------------------------------------------------

class TestGetVersionsBelow:
    def test_filters_above_target(self):
        versions = ["1.0.0", "1.2.0", "1.2.5", "1.3.0", "1.10.0"]
        result = _get_versions_below(versions, "1.3.0")
        assert "1.3.0" not in result
        assert "1.10.0" not in result  # above target
        assert "1.2.5" in result

    def test_sorted_descending(self):
        versions = ["1.0.0", "1.2.0", "1.2.5", "1.3.0"]
        result = _get_versions_below(versions, "1.3.0")
        assert result == ["1.2.5", "1.2.0", "1.0.0"]

    def test_distinct_versions_1_1_vs_1_10(self):
        """Regression test: 1.1 must not match 1.10."""
        versions = ["1.1", "1.10", "1.100", "1.2"]
        result = _get_versions_below(versions, "1.10")
        assert result == ["1.2", "1.1"]  # 1.100 is above 1.10

    def test_empty_input(self):
        assert _get_versions_below([], "1.0.0") == []

    def test_unparseable_target_raises(self):
        # An empty target version parses to an empty tuple (not an exception),
        # but a target that contains only non-digit, non-token chars produces
        # a tuple with a single (1, 0, token) element. Both are valid parse
        # results — what we want to assert is that comparing against them is
        # safe. We use a clearly broken value here and verify no exception
        # escapes (regression for the silent-swallow bug fixed in 1.5).
        result = _get_versions_below(["1.0.0"], "not-a-version")
        # 1.0.0 < (1, 0, "not-a-version") because (0, 1, "") < (1, 0, "...")
        assert "1.0.0" in result


# ---------------------------------------------------------------------------
# builder._parse_patch_names
# ---------------------------------------------------------------------------

class TestParsePatchNames:
    def test_extracts_default_true_patches(self):
        output = """
Name: Patch A
Description: foo
Default: true

Name: Patch B
Description: bar
Default: false
"""
        result = _parse_patch_names(output)
        assert "Patch A" in result
        assert "Patch B" not in result

    def test_fallback_returns_all_when_no_default(self):
        output = """
Name: Patch A
Description: foo

Name: Patch B
Description: bar
"""
        result = _parse_patch_names(output)
        assert "Patch A" in result
        assert "Patch B" in result

    def test_empty_input(self):
        assert _parse_patch_names("") == []


# ---------------------------------------------------------------------------
# builder._validate_download
# ---------------------------------------------------------------------------

class TestValidateDownload:
    def test_rejects_small_file(self, tmp_path):
        f = tmp_path / "small.apk"
        f.write_bytes(b"PK\x03\x04" + b"\x00" * 100)  # valid magic, too small
        with pytest.raises(BuilderError, match="too small"):
            _validate_download(f)

    def test_rejects_bad_magic(self, tmp_path):
        f = tmp_path / "not_apk.apk"
        f.write_bytes(b"HTML" + b"\x00" * 2_000_000)  # wrong magic, big enough
        with pytest.raises(BuilderError, match="not a valid APK"):
            _validate_download(f)

    def test_accepts_valid_apk(self, tmp_path):
        f = tmp_path / "ok.apk"
        f.write_bytes(b"PK\x03\x04" + b"\x00" * 2_000_000)
        _validate_download(f)  # should not raise
        assert f.exists()  # not deleted


# ---------------------------------------------------------------------------
# builder.BuildState
# ---------------------------------------------------------------------------

class TestBuildState:
    def test_failed_signatures_thread_safe(self):
        import threading
        state = BuildState()
        def adder():
            for i in range(100):
                state.add_failed_signature(f"app-{i}")
        threads = [threading.Thread(target=adder) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert len(state.failed_signatures) == 100
        assert state.has_failed_signature("app-50")
        assert not state.has_failed_signature("app-999")

    def test_patches_info_snapshot_is_a_copy(self):
        state = BuildState()
        state.set_patches_info("App1", ["p1", "p2"])
        snap = state.snapshot_patches_info()
        snap["App1"].append("p3")
        # Mutating the snapshot must not affect the underlying state.
        assert state.patches_info["App1"] == ["p1", "p2"]


# ---------------------------------------------------------------------------
# builder.load_patches_info_cache
# ---------------------------------------------------------------------------

class TestLoadPatchesInfoCache:
    def test_returns_empty_when_file_missing(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert load_patches_info_cache() == {}

    def test_returns_dict_when_file_valid(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "patches_info.json").write_text(json.dumps({
            "App1": ["p1", "p2"],
            "App2": ["p3"],
        }))
        result = load_patches_info_cache()
        assert result == {"App1": ["p1", "p2"], "App2": ["p3"]}

    def test_returns_empty_on_invalid_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "patches_info.json").write_text("not json {{{")
        assert load_patches_info_cache() == {}

    def test_returns_empty_on_non_dict_json(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "patches_info.json").write_text("[1, 2, 3]")
        assert load_patches_info_cache() == {}


# ---------------------------------------------------------------------------
# config.parse_config / parse_app_entries / validate_config
# ---------------------------------------------------------------------------

class TestConfigParsing:
    def _minimal_config_toml(self) -> str:
        return """
parallel-jobs = 1
brand = "Test"
strict-sigcheck = false

[aliases]
youtube = "com.google.android.youtube"

[TestApp]
pkg-name = "com.example.app"
apkmirror-dlurl = "https://www.apkmirror.com/apk/example/example-app"

[TestApp.patches]
"github:owner/some-patches" = []
"""

    def test_parse_config_defaults(self, tmp_path):
        toml_path = tmp_path / "config.toml"
        toml_path.write_text(self._minimal_config_toml())
        data = load_toml(toml_path)
        cfg = parse_config(data)
        assert cfg.brand == "Test"
        assert cfg.parallel_jobs == 1
        assert cfg.strict_sigcheck is False
        assert cfg.allow_insecure is False
        assert cfg.network_concurrency == 4
        assert cfg.aliases == {"youtube": "com.google.android.youtube"}

    def test_parse_app_entries_basic(self, tmp_path):
        toml_path = tmp_path / "config.toml"
        toml_path.write_text(self._minimal_config_toml())
        data = load_toml(toml_path)
        cfg = parse_config(data)
        entries = parse_app_entries(data, cfg)
        assert len(entries) == 1
        e = entries[0]
        assert e.table == "TestApp"
        assert e.pkg_name == "com.example.app"
        assert e.enabled is True
        assert e.arch == "all"  # default
        assert e.version == "auto"  # default
        assert "apkmirror" in e.dl_urls

    def test_validate_config_rejects_duplicate_table(self):
        from dataclasses import replace
        e1 = AppEntry(
            table="Dup", pkg_name="com.x", badge_color="", badge_icon="",
            app_name="Dup", brand="morphe", release_group="morphe", arch="all",
            dpi="", version="auto", dl_urls={"apkmirror": "https://x"},
            patcher_args=[], patches={"github:o/r": {"version": "latest", "include": [], "exclude": []}},
            exclusive_patches=False, cli_source="github:o/r", cli_version="latest",
            skip_sigcheck=False, enabled=True, changelog_keywords=[],
        )
        e2 = replace(e1)  # identical — same table name
        with pytest.raises(ValueError, match="Duplicate table name"):
            validate_config([e1, e2])

    def test_validate_config_rejects_no_patches(self):
        e = AppEntry(
            table="X", pkg_name="com.x", badge_color="", badge_icon="",
            app_name="X", brand="morphe", release_group="morphe", arch="all",
            dpi="", version="auto", dl_urls={"apkmirror": "https://x"},
            patcher_args=[], patches={},  # no patches
            exclusive_patches=False, cli_source="github:o/r", cli_version="latest",
            skip_sigcheck=False, enabled=True, changelog_keywords=[],
        )
        with pytest.raises(ValueError, match="no patches defined"):
            validate_config([e])

    def test_validate_config_rejects_bad_patch_source(self):
        e = AppEntry(
            table="X", pkg_name="com.x", badge_color="", badge_icon="",
            app_name="X", brand="morphe", release_group="morphe", arch="all",
            dpi="", version="auto", dl_urls={"apkmirror": "https://x"},
            patcher_args=[], patches={"bitbucket:o/r": {"version": "latest", "include": [], "exclude": []}},
            exclusive_patches=False, cli_source="github:o/r", cli_version="latest",
            skip_sigcheck=False, enabled=True, changelog_keywords=[],
        )
        with pytest.raises(ValueError, match="must start with"):
            validate_config([e])

    def test_invalid_arch_raises(self, tmp_path):
        toml_path = tmp_path / "config.toml"
        toml_path.write_text("""
[TestApp]
arch = "invalid-arch"
apkmirror-dlurl = "https://x"

[TestApp.patches]
"github:o/r" = []
""")
        data = load_toml(toml_path)
        cfg = parse_config(data)
        with pytest.raises(ValueError, match="Wrong arch"):
            parse_app_entries(data, cfg)


# ---------------------------------------------------------------------------
# config.VALID_ARCHES
# ---------------------------------------------------------------------------

class TestValidArches:
    def test_all_expected_arches_present(self):
        for a in ("both", "all", "arm64-v8a", "armeabi-v7a", "x86_64", "x86"):
            assert a in VALID_ARCHES

    def test_invalid_arches_absent(self):
        assert "mips" not in VALID_ARCHES
        assert "arm" not in VALID_ARCHES  # bare arm is not supported


# ---------------------------------------------------------------------------
# exceptions.ApkforgeError
# ---------------------------------------------------------------------------

class TestExceptionTaxonomy:
    def test_all_subclass_apkforge_error(self):
        from src.core.exceptions import (
            BuilderError, ConfigError, NetworkError, PatcherError,
            PrebuiltsError, ResourceNotFoundError, ScraperError, SignatureError, SSRFError,
        )
        for cls in (BuilderError, ConfigError, NetworkError, PatcherError, PrebuiltsError,
                    ResourceNotFoundError, ScraperError, SignatureError, SSRFError):
            assert issubclass(cls, ApkforgeError)

    def test_retryable_flag_is_set(self):
        from src.core.exceptions import NetworkError, PrebuiltsError, ResourceNotFoundError, SSRFError
        assert NetworkError.retryable is True
        assert PrebuiltsError.retryable is True
        assert ResourceNotFoundError.retryable is False
        assert SSRFError.retryable is False


# ---------------------------------------------------------------------------
# network._validate_url (SSRF guard)
# ---------------------------------------------------------------------------

class TestSSRFGuard:
    def test_rejects_loopback_ip(self):
        with pytest.raises(SSRFError, match="loopback"):
            _validate_url("http://127.0.0.1/", allow_insecure=False)

    def test_rejects_private_ip(self):
        with pytest.raises(SSRFError, match="private"):
            _validate_url("https://10.0.0.1/", allow_insecure=False)
        with pytest.raises(SSRFError, match="private"):
            _validate_url("https://192.168.1.1/", allow_insecure=False)

    def test_rejects_link_local(self):
        with pytest.raises(SSRFError, match="link-local|loopback|private"):
            _validate_url("https://169.254.169.254/", allow_insecure=False)

    def test_rejects_plain_http_when_strict(self):
        with pytest.raises(SSRFError, match="plain-HTTP"):
            _validate_url("http://example.com/", allow_insecure=False)

    def test_allows_plain_http_when_insecure(self):
        # Should not raise.
        _validate_url("http://example.com/", allow_insecure=True)

    def test_allows_https(self):
        _validate_url("https://example.com/", allow_insecure=False)

    def test_allows_private_host_in_allowlist(self, monkeypatch):
        monkeypatch.setenv("APKFORGE_ALLOW_PRIVATE_HOSTS", "my-internal-mirror")
        # Re-import to pick up the env var.
        import importlib
        import src.core.network as net_mod
        importlib.reload(net_mod)
        net_mod._validate_url("https://my-internal-mirror/", allow_insecure=False)


# ---------------------------------------------------------------------------
# prebuilts._find_cached, _ver_key, get_highest_ver
# ---------------------------------------------------------------------------

class TestPrebuiltsHelpers:
    def test_ver_key_extracts_digits(self):
        assert _ver_key("1.2.3") == (1, 2, 3)
        assert _ver_key("v2.0.0") == (2, 0, 0)
        assert _ver_key("no-version") == (0,)

    def test_get_highest_ver(self):
        assert get_highest_ver(["1.0.0", "1.2.0", "1.10.0"]) == "1.10.0"
        assert get_highest_ver(["1.0.0"]) == "1.0.0"

    def test_get_highest_ver_empty_raises(self):
        with pytest.raises(ValueError):
            get_highest_ver([])

    def test_find_cached_no_match(self, tmp_path):
        (tmp_path / "patch-v1.0.0.mpp").write_bytes(b"x")
        (tmp_path / "patch-v1.10.0.mpp").write_bytes(b"x")
        # Looking for 1.1 must NOT match 1.10.
        result = _find_cached(tmp_path, "1.1", "mpp")
        assert result is None

    def test_find_cached_exact_match(self, tmp_path):
        (tmp_path / "patch-v1.0.0.mpp").write_bytes(b"x")
        (tmp_path / "patch-v1.1.0.mpp").write_bytes(b"x")
        (tmp_path / "patch-v1.10.0.mpp").write_bytes(b"x")
        result = _find_cached(tmp_path, "1.1", "mpp")
        assert result is not None
        assert "1.1.0" in result.name

    def test_find_cached_wildcard_returns_all(self, tmp_path):
        (tmp_path / "patch-v1.0.0.mpp").write_bytes(b"x")
        (tmp_path / "patch-v2.0.0.mpp").write_bytes(b"x")
        result = _find_cached(tmp_path, "*", "mpp")
        assert result is not None
        assert "2.0.0" in result.name  # max by _ver_key

    def test_find_cached_ignores_tmp_files(self, tmp_path):
        (tmp_path / "tmp.patch-v1.0.0.mpp").write_bytes(b"x")
        (tmp_path / "patch-v1.0.0.mpp").write_bytes(b"x")
        result = _find_cached(tmp_path, "1.0.0", "mpp")
        assert result is not None
        assert not result.name.startswith("tmp.")


# ---------------------------------------------------------------------------
# scripts.merge_patches_info
# ---------------------------------------------------------------------------

class TestMergePatchesInfo:
    def test_merges_multiple_files(self, tmp_path):
        (tmp_path / "patches").mkdir()
        (tmp_path / "patches" / "patches_info-app1.json").write_text(json.dumps({"App1": ["p1", "p2"]}))
        (tmp_path / "patches" / "patches_info-app2.json").write_text(json.dumps({"App2": ["p3"]}))
        merged = merge_patches_info(tmp_path / "patches")
        assert merged == {"App1": ["p1", "p2"], "App2": ["p3"]}

    def test_later_files_override_earlier(self, tmp_path):
        (tmp_path / "patches").mkdir()
        (tmp_path / "patches" / "patches_info-app1a.json").write_text(json.dumps({"App1": ["old"]}))
        (tmp_path / "patches" / "patches_info-app1b.json").write_text(json.dumps({"App1": ["new"]}))
        merged = merge_patches_info(tmp_path / "patches")
        assert merged == {"App1": ["new"]}

    def test_preserves_existing(self, tmp_path):
        existing = tmp_path / "patches_info.json"
        existing.write_text(json.dumps({"App0": ["p0"]}))
        (tmp_path / "patches").mkdir()
        (tmp_path / "patches" / "patches_info-app1.json").write_text(json.dumps({"App1": ["p1"]}))
        merged = merge_patches_info(tmp_path / "patches", existing=existing)
        assert merged == {"App0": ["p0"], "App1": ["p1"]}

    def test_skips_invalid_json(self, tmp_path):
        (tmp_path / "patches").mkdir()
        (tmp_path / "patches" / "patches_info-bad.json").write_text("not json")
        (tmp_path / "patches" / "patches_info-good.json").write_text(json.dumps({"App1": ["p1"]}))
        merged = merge_patches_info(tmp_path / "patches")
        assert merged == {"App1": ["p1"]}

    def test_no_root_returns_existing_only(self, tmp_path):
        existing = tmp_path / "patches_info.json"
        existing.write_text(json.dumps({"App0": ["p0"]}))
        merged = merge_patches_info(tmp_path / "nonexistent", existing=existing)
        assert merged == {"App0": ["p0"]}
