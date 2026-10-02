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
| `main.py` | CLI parsing, `.env` loading, Java check, build dispatch, interrupt cleanup. Refreshes the WhatsApp pins only on CI (or with `APKFORGE_SYNC_WA_VERSION=1`) so a local build never rewrites `config.toml` |
| `src/core/config.py` | Parses `config.toml` into `Config` / `AppEntry` dataclasses and validates entries. Does **not** touch the filesystem at import time. |
| `src/core/builder.py` | The orchestrator. Resolves package names and versions, downloads stock APKs with fallbacks, applies patches with automatic exclusion/retry, trims bundles and writes reports. |
| `src/core/patcher.py` | Thin wrapper around the Morphe CLI: `list-patches`, `list-versions`, and `patch`. Streams patch output live with a timeout and redacts keystore secrets. |
| `src/core/prebuilts.py` | Fetches CLI jars and `.mpp` patch bundles from GitHub/GitLab releases, caching them under `temp/`. |
| `src/core/network.py` | Shared HTTP client with retries, per-domain rate-limiting locks, browser impersonation rotation, proxy/FlareSolverr support and GitHub auth headers. |
| `src/core/versions.py` | Centralised version helpers: `clean_version`, `parse_version`, `version_sort_key`, `highest_version`, `highest_tag`. |
| `src/core/logger.py` | Colourised local logging with GitHub Actions annotations, plus `require_ci`. |
| `src/scrapers/` | Per-source metadata + download strategies (`apkmirror`, `uptodown`, `github`, `direct`) behind the `BaseScraper` interface. |

## Build pipeline in detail

1. **Entry selection** — `main._build()` loads config and filters to `enabled` entries, optionally refreshing the WhatsApp pins from WaEnhancer (CI only; disabled entries are skipped).
2. **Package name** — `_find_pkg_name()` tries each configured source's metadata until it learns the package name, preferring the explicit `pkg-name`.
3. **Version** — `_resolve_version()`:
   - `auto`/`latest` consult the patcher for the last supported version;
   - a wildcard (`X.Y.Z.xx`) collects matches across **all** sources and picks the highest, falling back to the newest overall if nothing matches, then to the cache, then to `latest` — never to a synthetic `<prefix>.0` that no source ever published;
   - a pinned version is used verbatim.
4. **Download** — `_download_apk()` checks the local cache first — validating it too, so a truncated file is discarded rather than reused — then tries each source (the metadata source first), checking size and ZIP magic before accepting a file. For a wildcard pin the sources are walked twice: once enforcing the prefix, and if nothing matched, once without, so a mirror ships the newest build a source actually has instead of failing forever. The accepted artifact is then re-keyed in the cache to the version read from its manifest, so a placeholder-named download (`version = "nightly"`) is neither mislabelled nor deleted by the outdated-version sweep.
5. **Patch** — `_build_single()` opens a private per-build temp directory, runs the Morphe CLI through `PatcherCLI.patch` (streamed output), and searches that directory for alternate output filenames. If the CLI reports a failed patch, the patch is excluded and the build retried (up to 5 times).
6. **Optimize** — `_maybe_optimize_bundle()` trims split bundles to the target ABI plus English and `xxhdpi`. The `all` architecture is left untouched.
7. **Sign** — `PatcherCLI.patch` signs with the environment keystore, a local `morphe.keystore`, or the CLI debug key.
8. **Auto patches** — `resolve_auto_patches()` detects the bundle's "Disable Play Store updates" and GmsCore/MicroG patches by name. The former is always enabled; the latter only when the app sets `microg = true`, and is passed `-d` otherwise so a bundle default cannot silently make the app require MicroG. Neither is re-enabled if the retry loop has just excluded it.
9. **Report** — results are written to `build.json`, `build.md`, `patches_info.json` and `versions_info.json`. Exclusions recorded for the entries built in a run replace the previous ones, so a clean rebuild stops reporting a patch as excluded.

## Concurrency and caching

- Builds run in a `ThreadPoolExecutor` sized by `parallel-jobs`; each build gets its own temp directory so parallel builds of the same app cannot collide.
- `NetworkManager` serialises requests per domain and per destination path, making concurrent downloads safe.
- Stock APKs persist in `unmodified-apks/` and are cached between CI runs via `actions/cache`, keyed per app.
- CLI jars and `.mpp` bundles live under `temp/<org>/`. Because that directory is shared per organisation, every asset a run fetches or reuses is *claimed*, so fetching a second `(repo, version)` pair from the same org cannot evict a bundle still referenced by `mpp_map`.

## Bot protection

Many sources (notably APKMirror and Uptodown) are fronted by Cloudflare. `NetworkManager` handles this in layers:

1. **Browser impersonation** — each request is made with a `curl_cffi` browser target (Chrome/Firefox/Edge/Safari), matching TLS, HTTP/2 and default headers. Rotation swaps the whole session under a reader/writer lock so the User-Agent is never overridden by hand (the FlareSolverr UA excepted).
2. **Navigation headers** — `Accept`, `Accept-Language`, `Sec-Fetch-*` (with the correct `same-origin`/`cross-site`/`none` value) and chained `Referer` values are sent like a real browser session.
3. **Warm-up & rotation** — a challenge is a `cf-mitigated` header or an interstitial body marker; `Server: cloudflare` alone is *not* treated as one, to avoid misclassifying ordinary blocked responses. The domain root is fetched once to acquire cookies; failing that, the impersonation target is rotated. Mitigations draw on their **own** bounded allowance rather than the HTTP retry budget, so every impersonation really is tried before a source is called unreachable.
4. **FlareSolverr** — when `FLARESOLVERR_URL` is set, managed challenges (`cf-mitigated`/Turnstile) are solved in a real browser; the returned HTML and cookies (including `cf_clearance`) are merged in and reused. For downloads the solver is asked for cookies only (`returnOnlyCookies`) against the real asset URL, so the binary is never pulled through it.
5. **Proxy** — `APKFORGE_PROXY` (or the standard `*_PROXY` variables) routes all traffic through a proxy; a residential/mobile proxy is the most reliable fix for blocked IP ranges.

`Retry-After` is honoured for `429`/`503`. `404` and `410` are permanent by definition and fail over immediately instead of consuming retries. Challenge and HTTP failures fall back to the next configured source.

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
