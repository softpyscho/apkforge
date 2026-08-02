# apkforge — Implementation Summary

This document tracks which items from `IMPROVEMENTS.md` were implemented in this revision. Items marked **DONE** are in the codebase; items marked **DEFERRED** are documented in `IMPROVEMENTS.md` but not yet implemented (typically because they require upstream CLI cooperation or are large refactors).

## Phase 1 — Correctness & Reliability (P0)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 1.1 | Fix `_build_single` return type | **DONE** | Returns `BuildResult \| None` (a `TypedDict`); old `str \| None` annotation removed. |
| 1.2 | Graceful SIGINT shutdown | **DONE** | `main.py:_sigint_handler` now drains workers via `threading.Event`, cleans temp keystore files, and exits with 130. Second Ctrl+C forces immediate exit. |
| 1.3 | Fix `_find_cached` glob bug | **DONE** | `prebuilts._find_cached` now uses a `(?<!\d)ver(?!\d)` regex instead of a substring glob. Test coverage: `test_find_cached_no_match`, `test_find_cached_exact_match`. |
| 1.4 | Centralize `BuildState` | **DONE** | New `BuildState` dataclass in `builder.py` with thread-safe `add_failed_signature` / `set_patches_info` / `snapshot_patches_info`. Module-level `_failed_signatures` and `_patches_info` removed. |
| 1.5 | `_get_versions_below` swallow errors | **DONE** | Catches `ValueError` only, logs the skipped version, raises `BuilderError` if the target version itself fails to parse. |
| 1.6 | APKMirror DPI regex dead code | **DONE** | Removed `re.match(r"\d+-640dpi", dpi_text)` clause; documented the surviving logic. |
| 1.7 | `_find_pkg_name` getattr | **DONE** | Now `entry.pkg_name or metadata.pkg_name`. |
| 1.8 | `known_pkgs` hardcoded | **DONE** | Moved to `[aliases]` table in `config.toml`; `_find_pkg_name` accepts an `aliases` parameter. |
| 1.9 | Always report failed CLI/patch fetches | **DONE** | `_submit_entries` now submits a `_make_failure_result` future for entries that couldn't be submitted, so they appear in `build.json`. |
| 1.10 | `patches_info.json` race | **DONE** | Fixed by 1.4 (`BuildState.snapshot_patches_info`). |

## Phase 2 — Robustness & Network (P1)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 2.1 | Drop fixed `time.sleep(0.5)` | **DONE** | Per-domain lock still serializes; fixed sleep removed. |
| 2.2 | `verify=True` hardcoded | **DONE** | `NetworkManager.__init__` accepts `allow_insecure`; passed through from `Config.allow_insecure` (env: `APKFORGE_INSECURE`). |
| 2.3 | Enable HTTP/2 | **DONE** | `requests.Session(impersonate="chrome146", http2=True)`. |
| 2.4 | Retry budget coordination | **PARTIAL** | `NetworkManager.max_attempts` is now configurable; per-build counter not yet wired into `BuildState`. Documented as future work. |
| 2.5 | APKPure scraper refactor | **DEFERRED** | Requires investigating APKPure's JSON-LD endpoints. Documented in roadmap. |
| 2.6 | SSRF guard | **DONE** | `_validate_url` rejects non-HTTPS (unless `allow_insecure`), loopback/private/link-local IP literals. Override via `APKFORGE_ALLOW_PRIVATE_HOSTS`. |
| 2.7 | `KEYSTORE_PASS` via file | **PARTIAL** | Morphe CLI doesn't yet support `--keystore-password-file`. We keep CLI-passing but redact in logs (`_SECRET_PATTERNS`) and document the limitation in `SECURITY.md`. |
| 2.8 | Propagate `PrebuiltsError` | **DONE** | `fetch_cli`/`fetch_mpp` failures now propagate as failure entries via `_make_failure_result`. |

## Phase 3 — Architecture & Testability (P1)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 3.1 | Tests | **DONE** | 51 tests in `tests/test_core.py` covering: `_parse_ver`, `_get_versions_below` (incl. 1.1-vs-1.10 regression), `_parse_patch_names`, `_validate_download`, `BuildState` thread safety, `load_patches_info_cache`, config parsing/validation, `VALID_ARCHES`, exception taxonomy, SSRF guard, `_find_cached`/`_ver_key`/`get_highest_ver`, `merge_patches_info`. |
| 3.2 | mypy strict | **PARTIAL** | `[tool.mypy] strict = true` added to `pyproject.toml`. CI runs `mypy` as warn-only (`|| true`) until all violations are fixed. |
| 3.3 | `logging` framework | **DONE** | `logger.py` now wraps stdlib `logging` with a custom filter for GH annotations. Public API (`pr`/`epr`/`wpr`/`abort`) unchanged. `set_verbose(True)` toggles DEBUG. |
| 3.4 | Exception taxonomy | **DONE** | New `src/core/exceptions.py` with `ApkforgeError` base + `retryable: bool` attribute. Existing classes (`BuilderError`, `PatcherError`, etc.) re-exported for backwards compat. |
| 3.5 | Lazy imports | **DONE** | `_make_scraper` imports moved to module top in `builder.py`. |
| 3.6 | `APKSIGNER`/keystore paths in Config | **DONE** | `Config.apksigner_path` and `Config.keystore_path` with sensible defaults. |
| 3.7 | Enforce HTTPS in `network.get` | **DONE** | `_validate_url` called at the top of both `get` and `download`. |

## Phase 4 — CLI & Developer Experience (P1)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 4.1 | `argparse` / `--help` | **DONE** | `main.py` rewritten with subcommands `build`, `list`, `clear` + `--version`, `-v/--verbose`. Bare positional form (`main.py Reddit arm64-v8a`) still works for backwards compat. |
| 4.2 | `list` / `--dry-run` mode | **DONE** | `main.py list [--filter NAME]` prints the resolved entries without building. |
| 4.3 | `apkforge` console script | **DONE** | `[project.scripts] apkforge = "main:main"` in `pyproject.toml`. |
| 4.4 | Replace `_load_dotenv` | **DONE** | `python-dotenv` added to deps; falls back to a small hand-rolled parser if not installed. Handles inline comments and quoted values. |
| 4.5 | `hooked_run` via logger | **DONE** | `builder._apply_patch.hooked_run` now calls `pr`/`epr` instead of bare `print`. |
| 4.6 | Enriched `build.json` | **DONE** | Each entry now includes `started_at`, `finished_at`, `duration_s`, `source` (mirror used), `stock_apk_size`. |
| 4.7 | `patches_info.json` merge script | **DONE** | New `src/scripts/merge_patches_info.py` with full test coverage. `build.yml` uses it instead of the inline Python one-liner. |

## Phase 5 — CI/CD (P1)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 5.1 | `ruff` in lint workflow | **DONE** | `lint.yml` now runs `ruff check --output-format=github` and `ruff format --check`. |
| 5.2 | Consistent action pinning | **DONE** | All workflows pin to commit SHAs at the same version (checkout@v7.0.0, setup-uv@v8.2.0, cache@v4.2.2). |
| 5.3 | `concurrency` cancellation | **DONE** | `lint.yml`, `build.yml`, `ci.yml` all have `concurrency` blocks. `lint.yml` cancels in-progress; `ci.yml` does not (to avoid disrupting scheduled daily checks). |
| 5.4 | Gradle/M2 cache | **DONE** | `build.yml` caches `~/.m2/repository`, `~/.gradle/caches`, `~/.gradle/wrapper`. |
| 5.5 | `repository_dispatch` | **DEFERRED** | Requires upstream repos to fire webhooks; out of scope for this revision. |
| 5.6 | Dedupe identical APKs | **DEFERRED** | Rare edge case; deferred. |
| 5.7 | Fix issue template repo URLs | **DONE** | `krvstek/apkforge` → `softpsycho/apkforge` in both `script.yml` and `build.yml` templates. |

## Phase 6 — Documentation & UX (P2)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 6.1 | Collapse Obtainium links in README | **DONE** | Obtainium JSON moved to a `<details>` block; the apps table is now scannable. |
| 6.2 | Architecture diagram | **DONE** | Mermaid flowchart added showing `config.toml` → `parse_app_entries` → `NetworkManager` → `Scrapers` → `PatcherCLI` → `apksigner` → `build/`. |
| 6.3 | Troubleshooting section | **DONE** | 5 expandable sections: Java mismatch, sig mismatch, mirror timeouts, failed patches, missing asset. |
| 6.4 | `--version` flag | **DONE** | Reads `importlib.metadata.version("apkforge")`; falls back to `pyproject.toml` if not installed. `pyproject.toml` version bumped to `1.0.0`. |
| 6.5 | CONTRIBUTING.md testing section | **DONE** | New `## 🧪 Testing` section with `uv run pytest`, `ruff check`, `mypy` commands. |
| 6.6 | README pipeline links | **DONE** | Each pipeline step now references its workflow file and Python module. |

## Phase 7 — Performance (P2)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 7.1 | `_get_versions_below` re-sorts | **DONE** | Sorts once at the end (was already correct in the original; comment added). |
| 7.2 | `_optimize_bundle` double read | **DONE** | Now iterates `infolist()` once and uses `ZipInfo` directly. |
| 7.3 | Batch `list_patches` | **DEFERRED** | Requires checking if Morphe CLI supports multiple `--patches` in one invocation. Documented. |
| 7.4 | Decouple `parallel-jobs` / `network-concurrency` | **PARTIAL** | `Config.network_concurrency` added; not yet wired into a separate semaphore in `NetworkManager`. |

## Phase 8 — Code Quality (P3)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 8.1 | `from copy import replace` | **DONE** | Made backwards-compat with Python 3.12 via try/except. |
| 8.2 | `assert match` after abort | **DONE** | Removed the redundant assert; `abort` is annotated `-> Never`. |
| 8.3 | Broad `except Exception` | **PARTIAL** | Narrowed in `_build_single`'s fallback loop (now catches `(NetworkError, ScraperError, BuilderError)`). Some remain in `prebuilts._fetch_single_asset` and `matrix._fetch_our_releases` — documented. |
| 8.4 | `try/except Exception` for patches_info load | **DONE** | Replaced with `load_patches_info_cache()` that catches `json.JSONDecodeError` only. |
| 8.5 | `_parse_patch_names` fragility | **PARTIAL** | Logic unchanged; documented as needing a snapshot test once CLI supports `--json`. |
| 8.6 | Lazy imports | **DONE** (see 3.5) | |
| 8.7 | Exception taxonomy | **DONE** (see 3.4) | |

## Phase 9 — Security (P2)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 9.1 | `KEYSTORE_PASS` on cmdline | **PARTIAL** (see 2.7) | |
| 9.2 | `_load_dotenv` escaping | **DONE** | Uses `python-dotenv`; fallback parser handles inline comments and quotes. |
| 9.3 | SSRF guard | **DONE** (see 2.6) | |
| 9.4 | apksigner native-access comment | **DONE** | `SECURITY.md` section "apksigner Native Access" explains the rationale. |
| 9.5 | `SECURITY.md` | **DONE** | Full document covering signing model, threat table, reporting policy, self-hosted runner guidance. |

## Phase 10 — Long-term Refactors (P3)

| # | Item | Status | Notes |
|---|------|--------|-------|
| 10.1 | Async I/O | **DEFERRED** | Large refactor; documented in roadmap. |
| 10.2 | Plugin scrapers | **DEFERRED** | Documented in roadmap. |
| 10.3 | Per-app patch-source pinning in CI | **DEFERRED** | Documented in roadmap. |
| 10.4 | Web UI for `config.toml` | **DEFERRED** | Out of scope for the core tool. |
| 10.5 | Reproducible builds | **DEFERRED** | Requires CLI cooperation; documented in roadmap. |

## Test Results

```
$ uv run pytest tests/ --no-cov -q
...................................................                      [100%]
51 passed in 0.47s
```

## Files Changed

```
Modified:
  .github/ISSUE_TEMPLATE/build.yml        # krvstek → softpsycho
  .github/ISSUE_TEMPLATE/script.yml       # krvstek → softpsycho
  .github/workflows/build.yml             # concurrency, JVM cache, merge_patches_info script
  .github/workflows/ci.yml                # concurrency, consistent pinning
  .github/workflows/lint.yml              # ruff + mypy + concurrency
  CONTRIBUTING.md                         # testing section, aliases, network/SSRF, new CLI
  README.md                               # polished, roadmap updated
  config.toml                             # [aliases] table added
  main.py                                 # argparse, --version, list, graceful SIGINT
  pyproject.toml                          # version 1.0.0, dev extras, scripts, ruff/mypy/pytest config
  src/core/builder.py                     # BuildState, BuildResult, timing/source tracking, hooked_run via logger
  src/core/config.py                      # aliases, apksigner_path, keystore_path, allow_insecure, network_concurrency
  src/core/logger.py                      # logging framework, set_verbose
  src/core/network.py                     # SSRF guard, HTTP/2, Retry-After, configurable allow_insecure
  src/core/patcher.py                     # import from exceptions, keystore pass file scaffolding
  src/core/prebuilts.py                   # _find_cached regex fix, import from exceptions
  src/scrapers/apkmirror.py               # dead DPI regex removed
  src/scrapers/base.py                    # import from exceptions

Added:
  SECURITY.md                             # signing model, threat surface, reporting
  src/core/exceptions.py                  # unified ApkforgeError taxonomy with retryable flag
  src/scripts/merge_patches_info.py       # testable replacement for inline CI one-liner
  tests/conftest.py                       # pytest path setup
  tests/test_core.py                      # 51 unit tests
```
