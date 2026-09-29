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

### Tests

- Added regression tests for all of the above: `_find_pkg_name()` source precedence,
  `_write_versions_info()` exclusion handling, the WaEnhancer placeholder guard, the three
  release-note defects in `combine_logs()`, and single-pair README marker substitution.
  Suite: 57 -> 68 tests.

### Docs

- `README.md`: corrected the download-source order (fixed `direct` -> `github` -> `apkmirror` ->
  `uptodown`, cache first), the `parallel-jobs` default on CI, what `main.py clear` removes, the
  patch-retry budget and the `version` key semantics. Added Continuous Integration, Project
  Structure, Development & Testing and Limitations sections.
- `CONTRIBUTING.md`: replaced the claim that download sources are tried in the order their
  `*-dlurl` keys appear in the table — the order is fixed in `src/core/config.py`.

### Known limitations

- `build.yml` computes a `prerelease` flag from the patch bundles in use and passes it to the
  release step as `PRERELEASE`, but `gh release edit` never consumes it, so every release is
  published as a normal release. Applying the flag was deliberately **not** done here: GitHub's
  `releases/latest` endpoint skips pre-releases, and `matrix.py::_fetch_our_releases()` relies on it
  to decide whether a build is needed — marking every release as a pre-release would make the daily
  cron rebuild everything unconditionally. Fixing this needs a change to the update check too.
- `wa_version._get_highest_ver()` takes the last `<item>` in the upstream array rather than the
  highest version. It is correct for the current upstream ordering (ascending) and was therefore
  left alone.
- `_cleanup_outdated_apks()` takes an `arch` argument that it never uses.
