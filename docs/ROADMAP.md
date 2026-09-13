# Roadmap & Recommendations

A living list of improvements that would make apkforge a more complete APK mirroring and Morphe patching utility. Items are grouped by impact and roughly ordered.

## High impact

- **Re-introduce APKPure with a verified scraper.** APKPure support was removed because its selectors were guessed and could not be validated (the site returns HTTP 403 to plain fetches). A maintained implementation using `curl_cffi` impersonation, or its public API endpoints, would restore a large catalogue.
- **Per-app build-artifact manifest.** Emit a machine-readable manifest (package, version, versionCode, SHA-256 of the built APK, applied patches, source URL) alongside each release. This makes downstream verification, Obtainium pinning and auditing far easier.
- **Resumable / incremental builds.** Cache both the stock APK *and* the patched output keyed by `(pkg, version, patches-hash)` so re-runs skip unchanged work.
- **Cache warmer.** Pre-download stock APKs on a schedule so release builds are fast and resilient to source outages.

## Correctness & robustness

- **Proper version extraction.** `extract_apk_version` scans AXML strings heuristically. Using the manifest attribute (`android:versionName`) via a small parser or `aapt2` would be exact.
- **Structured source results.** Return typed `DownloadResult` metadata (version, versionCode, ABI, DPI) from every scraper so the builder can verify the downloaded artifact matches the requested version rather than relying on filename heuristics.
- **Retry/backoff budget per source.** Track per-source failure rates and temporarily deprioritise sources that fail repeatedly within a run.
- **Zip-slip and size guards.** Validate bundle member paths before extraction and cap decompressed size to protect against malicious archives.
- **Schema for `config.toml`.** Ship a JSON Schema and validate in CI for editor completion and early errors.

## Developer experience

- **Type checking in CI.** Add `mypy`/`pyright` (the code is already typed) alongside ruff and the unit tests.
- **Coverage reporting.** Publish coverage from the unittest suite.
- **Pre-commit hooks.** Run ruff format + check and the test suite before commits.
- **Golden-file tests for the README generator.** Snapshot the generated app table so formatting changes are reviewed.
- **Docker/devcontainer.** Pin Java + Python + uv for reproducible local builds.

## Feature ideas

- **APKMirror mirror mode for every app.** Allow `mirror = true` to bypass patching but still publish to GitHub Releases (already partly supported) with an optional "stock only" release channel.
- **Multiple release channels.** `stable` / `prerelease` / `nightly` channels driven by patch versions (`latest` vs `dev`).
- **Per-app signing keys.** Support distinct keystores per app for users who must match an existing signature.
- **Web dashboard.** A small static site generated from `versions_info.json` showing build history, patch lists and download links.
- **Notification integrations.** Beyond Telegram: Discord, ntfy, Matrix.
- **CLI version pinning by digest.** Resolve CLI/`.mpp` assets to checksums and record them in `versions_info.json` for supply-chain auditing.

## Operational

- **SHA pin all GitHub Actions** (done for the cache and lint workflows in this pass) and keep Dependabot enabled for `github-actions` and `uv`.
- **Stale-cache pruning policy.** The build workflow prunes old caches; move that into a scheduled job with a retention window.
- **Reproducible release notes.** Include a diff of patch lists between consecutive builds.
- **Metrics.** Record build durations and failure reasons per app to spot flaky sources.

## Deliberately out of scope

- Hash-based verification of stock APKs (`sig.txt`). This was removed along with `apksigner.jar`; signing remains, but signature *verification* is left to the user.
