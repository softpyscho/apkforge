# Changelog

All notable changes to apkforge. Dates are in `YYYY-MM-DD`.

## Unreleased

### Fixed

- **`config.toml` could be un-pinned from the supported WhatsApp versions.** When the WaEnhancer
  `supported_versions_wpp` / `supported_versions_business` arrays are missing, renamed or empty,
  `fetch_recommended_wa_versions()` falls back to the literal string `latest`, which
  `update_config_toml()` then wrote into the `[WhatsApp]` / `[WhatsApp-Business]` `version` keys —
  and CI commits that change. The mirrors would then track the newest stock release instead of the
  wildcard the patcher supports. Placeholder values are now rejected and the configured version is
  kept (`src/scripts/wa_version.py`).
- **A source that reports no package name no longer shadows the sources that know it.** The Direct
  scraper always returns an empty `pkg_name`, and `direct` is always tried first, so any app without
  an explicit `pkg-name` ran the whole build with an empty package name — disabling the
  `unmodified-apks/` cache, the cached-version fallbacks and outdated-APK pruning. `_find_pkg_name()`
  now keeps looking and only falls back to a nameless source when no source can supply a name
  (`src/core/builder.py`).
- **Stale patch exclusions are cleared.** `versions_info.json` kept a previous run's
  `excluded_patches` entries even after a clean rebuild, so the README and release notes went on
  striking through patches that were in fact applied. Exclusions for the entries built in a run are
  now replaced; entries not built in that run keep theirs (`src/core/builder.py`).
- **Release notes no longer label stock mirrors as "(Pending cache update)".** Mirror entries have no
  patch cache, so every published release rendered them that way instead of "(None - Stock Mirror)"
  (`src/scripts/logs.py`).
- **Release notes no longer report `arch = "all"` builds as `arm64-v8a`.** The architecture was
  hardcoded whenever the build label carried no `(arch)` suffix; it now comes from the entry's
  configuration (`src/scripts/logs.py`).
- **Release notes now mark excluded patches.** `combine_logs()` did not pass the excluded-patch
  cache to the shared renderer, so a patch dropped after a build error was listed as applied
  (`src/scripts/logs.py`).
- **Regenerating the README no longer overwrites a second mention of the generated-block markers.**
  The substitution applied to every marker pair in the file, so documenting the markers in prose got
  that prose replaced by a duplicate copy of the app table (`src/scripts/readme.py`).
- **The build-matrix changelog filter reads the bundle it will actually build.** `get_matrix()`
  always queried `releases/latest`, even for patch sources pinned to `version = "dev"`, so
  `changelog-keywords` were matched against the wrong release notes (`src/scripts/matrix.py`).
- **The README APK-source column no longer attributes another app's source.** The sidecar lookup
  globbed `*.src` when `pkg-name` was empty and hardcoded the cache directory instead of using
  `ORIGINAL_APK_DIR` (`src/scripts/readme.py`).
- **The stock APK cache is no longer destroyed by the build that filled it.** A download is named
  after the *requested* version, so for an entry whose version is a placeholder (`version = "nightly"`)
  the manifest-extracted version no longer matched the filename: `_cleanup_outdated_apks()` deleted
  the artifact that had just been fetched, and `_cached_apk_versions()` reported `nightly` as an
  available version — which outranks every real version in `highest_version()`. The cached artifact
  and its `.src` / `.orig` sidecars are now re-keyed to the extracted version, so a valid fallback
  remains on disk while placeholder-versioned entries still re-download every run
  (`src/core/builder.py`).
- **Hyphenated versions are no longer truncated when reading the cache.** `_cached_apk_versions()`
  cut the version at the first hyphen (`-v([^-]+)-`), so a cached `12.19.1-release.0` was reported
  as `12.19.1` and the cached-APK fallback asked its source for a version that never existed. Four
  apps currently build hyphenated versions (Twitter, Stremio, Mixplorer, Duck-Detector). The known
  trailing architecture is now stripped instead (`src/core/builder.py`).
- **The recommended WhatsApp version is chosen by version ordering, not array position.**
  `_get_highest_ver()` returned the last `<item>`, which is only correct while upstream stays sorted
  ascending. It now uses the shared `highest_version()` helper and reports an empty string — rather
  than a misleading `latest` — when the array is missing (`src/scripts/wa_version.py`).
- **Interrupting a build no longer leaves partial downloads behind.** The `SIGINT` handler swept only
  `temp/`, but `NetworkManager.download()` writes `tmp.<name>` next to the destination, i.e. into
  `unmodified-apks/` (`main.py`).

### Tests

- Added regression tests for all of the above: `_find_pkg_name()` source precedence,
  `_write_versions_info()` exclusion handling, the WaEnhancer placeholder guard, the three
  release-note defects in `combine_logs()`, single-pair README marker substitution, cache re-keying
  and hyphenated cached versions. Suite: 57 -> 74 tests.

### Docs

- `README.md`: corrected the download-source order (fixed `direct` -> `github` -> `apkmirror` ->
  `uptodown`, cache first), the `parallel-jobs` default on CI, what `main.py clear` removes, the
  patch-retry budget and the `version` key semantics. Added Continuous Integration, Project
  Structure, Development & Testing and Limitations sections.
- `CONTRIBUTING.md`: replaced the claim that download sources are tried in the order their
  `*-dlurl` keys appear in the table — the order is fixed in `src/core/config.py`.

### Known limitations

- `build.yml` computes a `prerelease` flag from the patch bundles in use and passes it to the release
  step as `PRERELEASE`, but `gh release edit` never consumes it, so every release is published as a
  normal release. This is left as-is deliberately, and the reason is now recorded next to the env var
  in `build.yml`: GitHub resolves `/releases/latest` to the newest **non**-pre-release release, and
  two things depend on that URL — `matrix.py::_fetch_our_releases()`, which would see no release and
  make the daily cron rebuild everything, and the Obtainium config of all 20 apps in `obtainium.json`
  and the README, which tracks `.../releases/latest` and would stop finding updates on every user's
  device. Wiring the flag up requires changing both first.
- `_version_from_cached_name()` recognises the architectures in `VALID_ARCHES`; a cache file written
  by an older revision with a different name shape falls back to cutting the version at the first
  hyphen.
- Uptodown downloads still require a browser solver (`FLARESOLVERR_URL`); unchanged by this work.
