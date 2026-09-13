# Architecture

This document explains how apkforge is put together. It is meant for contributors who want to understand or extend the pipeline.

## High-level flow

```
                        ┌────────────────────────────┐
                        │      ci.yml (cron)         │
                        │  detect upstream updates   │
                        └─────────────┬──────────────┘
                                      │ matrix
                                      ▼
                        ┌────────────────────────────┐
                        │      build.yml (run)       │
                        │   per-app build via main.py│
                        └─────────────┬──────────────┘
                                      ▼
   ┌────────────────────────── src/core/builder.py ──────────────────────────┐
   │                                                                          │
   │  resolve pkg name ─▶ resolve version ─▶ download stock APK ─▶ patch ─▶   │
   │  optimize bundle ─▶ sign (Morphe CLI) ─▶ copy to build/                  │
   │                                                                          │
   └──────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
                        ┌────────────────────────────┐
                        │   release job: merge logs, │
                        │   publish, update README   │
                        └────────────────────────────┘
```

## Modules

| Module | Responsibility |
|:-------|:---------------|
| `main.py` | CLI parsing, `.env` loading, Java check, WhatsApp version refresh, build dispatch |
| `src/core/config.py` | Parses `config.toml` into `Config` / `AppEntry` dataclasses and validates entries. Does **not** touch the filesystem at import time. |
| `src/core/builder.py` | The orchestrator. Resolves package names and versions, downloads stock APKs with fallbacks, applies patches with automatic exclusion/retry, trims bundles and writes reports. |
| `src/core/patcher.py` | Thin wrapper around the Morphe CLI: `list-patches`, `list-versions`, and `patch`. Streams patch output live with a timeout and redacts keystore secrets. |
| `src/core/prebuilts.py` | Fetches CLI jars and `.mpp` patch bundles from GitHub/GitLab releases, caching them under `temp/`. |
| `src/core/network.py` | Shared `curl_cffi` session with retries, per-domain rate-limiting locks and GitHub auth headers. |
| `src/core/versions.py` | Centralised version helpers: `clean_version`, `parse_version`, `version_sort_key`, `highest_version`, `highest_tag`. |
| `src/core/logger.py` | Colourised local logging with GitHub Actions annotations, plus `require_ci`. |
| `src/scrapers/` | Per-source metadata + download strategies (`apkmirror`, `uptodown`, `github`, `direct`) behind the `BaseScraper` interface. |

## Build pipeline in detail

1. **Entry selection** — `main._build()` loads config, filters enabled entries and optionally refreshes the WhatsApp wildcard versions from WaEnhancer.
2. **Package name** — `_find_pkg_name()` tries each configured source's metadata until it learns the package name, preferring the explicit `pkg-name`.
3. **Version** — `_resolve_version()`:
   - `auto`/`latest` consult the patcher for the last supported version;
   - a wildcard (`X.Y.Z.xx`) collects matches across **all** sources and picks the highest, only falling back to the newest overall if nothing matches;
   - a pinned version is used verbatim.
4. **Download** — `_download_apk()` checks the local cache first, then tries each source (the metadata source first), validating size and ZIP magic before accepting a file.
5. **Patch** — `_build_single()` opens a private per-build temp directory, runs the Morphe CLI through `PatcherCLI.patch` (streamed output), and searches that directory for alternate output filenames. If the CLI reports a failed patch, the patch is excluded and the build retried (up to 5 times).
6. **Optimize** — `_maybe_optimize_bundle()` trims split bundles to the target ABI plus English and `xxhdpi`. The `all` architecture is left untouched.
7. **Sign** — `PatcherCLI.patch` signs with the environment keystore, a local `morphe.keystore`, or the CLI debug key.
8. **Report** — results are written to `build.json`, `build.md`, `patches_info.json` and `versions_info.json`.

## Concurrency and caching

- Builds run in a `ThreadPoolExecutor` sized by `parallel-jobs`; each build gets its own temp directory so parallel builds of the same app cannot collide.
- `NetworkManager` serialises requests per domain and per destination path, making concurrent downloads safe.
- Stock APKs persist in `unmodified-apks/` and are cached between CI runs via `actions/cache`, keyed per app.

## Version semantics

`src/core/versions.py` exposes two comparison styles:

- `parse_version` / `highest_version` — APK versions. Numeric components sort before non-numeric suffix tokens, so `1.2.1-release.0` ranks above `1.2.1`.
- `version_sort_key` / `highest_tag` — release tags with mixed, mostly numeric names (`v1.10.0` > `v1.9.0`).

## CI

| Job | Description |
|:----|:------------|
| `check-versions` | Compares upstream patch releases and mirror stock versions with the last release. Prints `["all"]` or `[]`. |
| `prepare` | Creates/edits the draft release and computes the build matrix. |
| `run` | One matrix job per app/arch; uploads APKs, logs and per-app JSON to the draft. |
| `release` | Merges logs and metadata, publishes, notifies Telegram and commits README/Obtainium. |
