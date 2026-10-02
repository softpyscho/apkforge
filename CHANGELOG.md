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

- **A wildcard-pinned mirror no longer fails when no source carries that minor.** This is the
  WhatsApp failure in run `99159998844`: the resolver picked `2.26.37.74` from Uptodown's version
  list, the `direct` vendor source served a valid APK of a *different* minor, `_download_apk()`
  deleted it for not matching `2.26.37.xx`, and APKMirror (Cloudflare) plus Uptodown (Turnstile)
  both failed — so the build ended with "Stock APK not found" and could never publish again.
  Sources are now retried once with the wildcard not enforced, and the accepted artifact is
  relabelled with the version read from its manifest so it is never mislabelled
  (`src/core/builder.py`).
- **The wildcard fallback no longer fabricates a version.** With no candidates at all,
  `_resolve_version()` returned a synthetic `<prefix>.0` — `2.26.37.0` in the WhatsApp Business
  job — and every source was then asked for a version nobody ever published. It now falls back to
  `latest` and lets the manifest supply the real value (`src/core/builder.py`).
- **HTTP 410 Gone is no longer retried.** It is permanent by definition, but it was in the
  transient-retry set: the WhatsApp Business job spent roughly three minutes re-requesting a
  removed Uptodown page (8 attempts across two phases) before failing over
  (`src/core/network.py`).
- **Two versions of the same patch repo no longer delete each other's bundle.** `cl_dir` is
  per-**org**, so `github:crimera/piko` at `latest` (Twitter) and at `dev` (Instagram) shared
  `temp/crimera/`, and the second fetch's `*.mpp` eviction removed the first's file — leaving
  `mpp_map` pointing at a deleted path and failing that app. Reproduced with a stubbed releases
  API. Assets fetched or reused in a run are now claimed and never evicted
  (`src/core/prebuilts.py`).
- **Excluding a failing auto-detected patch now works.** `build_patch_args()` appended
  `-e <auto patch>` *after* `extra_args`, where the retry loop puts `-d <failed patch>`, so a
  failing GmsCore/MicroG or "Disable Play Store updates" patch was re-enabled on every retry and
  the build died with "failed again after being excluded" (`src/core/patcher.py`).
- **APKMirror downloads work after a failed metadata fetch.** `_category` was only set in
  `fetch_metadata()`, so on the builder's try-every-source fallback the release-page filter became
  `"//"` and never matched. It is now derived from the URL in `download()` too
  (`src/scrapers/apkmirror.py`).
- **A real app version starting with `7.1.`/`8.0.`/`9.0.` is no longer discarded.** The
  Android-platform-version guard in `extract_apk_version()` rejected those outright; they are now
  only deprioritised, and used when nothing else in the string pool looks like a version
  (`src/core/builder.py`).
- **A corrupt cached APK is no longer reused forever.** `_validate_download()` ran on fresh
  downloads only; the cache-reuse path now validates too and falls through to the sources
  (`src/core/builder.py`).
- **Cookie warm-up is claimed under a lock**, so concurrent builds warm a domain once
  (`src/core/network.py`).
- **A local build no longer rewrites the contributor's `config.toml`.** `main.py` synced the
  pinned WhatsApp versions on every full build; it is now CI-only, with
  `APKFORGE_SYNC_WA_VERSION=1` to opt in (the release job already does this sync and commits it).
- **Stock mirrors get a readable README badge.** Entries that set no `badge-color` / `badge-icon`
  produced `badge/Name-?logo=`; they now fall back to the project palette
  (`src/scripts/readme.py`).
- **"1 patches" is now "1 patch"** in the README and release notes.
- **A non-app top-level table in `config.toml` gets an actionable error** instead of
  "has no patches defined" (`src/core/config.py`).

- **Bot-challenge mitigations no longer exhaust the HTTP retry budget.** Every warm-up and every
  impersonation rotation counted against `_MAX_ATTEMPTS` (4), so of the six available mitigations
  only four ever ran and the rotation could never work through all five impersonations. Both
  WhatsApp job logs show it stopping at `edge101` and never reaching `safari184`. Verified against
  a source that only answers the last impersonation: before, `Request failed after 4 attempts`;
  after, it reaches `safari184` and succeeds in 7 requests. Mitigation retries now have their own
  bounded allowance (`src/core/network.py`).
- **APKMirror no longer searches for a literal `latest`.** With no concrete version resolved,
  `download()` requested `?s=latest`, which cannot match any release — visible in the WhatsApp
  Business log. An unspecific version now takes the newest release from the app's own listing, and
  the search is only used for a concrete version (`src/scrapers/apkmirror.py`).
- **A FlareSolverr container that fails to start no longer fails the build.** The startup step
  ended in `exit 1`, so enabling `USE_FLARESOLVERR=true` risked turning *every* matrix job red;
  it now warns and continues, and `FLARESOLVERR_URL` is exported only once the solver answers
  (`.github/workflows/build.yml`).

- **WhatsApp and WhatsApp Business are disabled** (`enabled = false` in `config.toml`). Both are
  pinned to the newest WaEnhancer-supported version, but the only source reachable from
  GitHub-hosted runners is `whatsapp.com`, which serves the newest build only — so run 55 published
  `whatsapp-mirror-v2.26.38.74` while WaEnhancer supported `2.26.34.xx`–`2.26.37.xx`, i.e. an APK
  WaEnhancer cannot hook. Business had no reachable source at all (Cloudflare on APKMirror,
  HTTP 410 on Uptodown). The README FAQ now documents tracking a WaEnhancer-supported version
  directly in Obtainium via the APKMirror source's `filterReleaseTitlesByRegEx` +
  `fallbackToOlderReleases`, which needs no build at all.
- **The WaEnhancer version sync skips disabled entries**, so CI no longer commits `config.toml`
  churn for apps that are not built (`src/scripts/wa_version.py`).

- **The GmsCore/MicroG patch is no longer forced on every app.** `resolve_auto_patches()` picked any
  patch whose name contained `gmscore` or `microg` out of the *full* `list-patches` output, and
  `build_patch_args()` then appended `-e <that patch>` for every build. Confirmed from the run
  logs: Xodo and Amazon Prime Video were patched with `-e MicroG integration` although neither
  config asks for it, which makes the installed app demand MicroG at runtime. Because the patch is
  not a *default* in that bundle it never appeared in `patches_info.json`, the README table or the
  release notes, so it was invisible. It is now opt-in per app via a new `microg` key (default
  `false`), and when off it is passed `-d` explicitly so a bundle that enables it by default cannot
  reintroduce the dependency (`src/core/config.py`, `src/core/patcher.py`, `src/core/builder.py`).
- **The "install MicroG-RE" line is only added to release notes when a built app opted in**
  (`src/core/builder.py`), and the README no longer lists a MicroG patch as applied for an app that
  did not opt in (`src/scripts/readme.py`).

- **Recovery and retry messages no longer raise GitHub error annotations.** `epr()` emits
  `::error::`, and it was used for every per-source failure and every successful mitigation, so one
  run reported **53 "errors"** in the Actions panel — most of them "Warmed up cookies…" and
  "Rotated impersonation…", i.e. the recovery working as designed. `network.py` no longer imports
  `epr` at all (retries and challenge handling are `wpr`/`pr`), and in `builder.py` the four
  per-source messages that fail over to another source became warnings. A build that actually
  failed, a missing CLI or patch bundle, and a config mistake stay errors. Replaying the Bitget
  challenge storm now yields 0 errors and 1 warning instead of 8 errors
  (`src/core/network.py`, `src/core/builder.py`).

### CI

- **`actions/cache` bumped to v5.1.0** (`caa2961`). v4.3.0 targets Node 20, which GitHub now
  force-runs on Node 24 and warns about on every job.
- **"Cache save failed" is gone.** A cache key is immutable, so when the stock APK had not changed
  `hashFiles()` produced a key that already existed and the save step failed on most jobs. The save
  is now skipped when the restored cache already carries that exact key, via the restore step's
  `cache-matched-key` output (`.github/workflows/build.yml`).
- **Greenify's `github-dlurl` removed.** It pointed at `releases/tag/com.oasisfeng.greenify` in this
  repository, which returns **HTTP 404** — verified against the API — so it failed on every run.
  Instagram's equivalent tag does exist and was left in place. Re-add Greenify's once a stock APK is
  published under that tag (`config.toml`).

### Tests

- Added regression tests for all of the above: `_find_pkg_name()` source precedence,
  `_write_versions_info()` exclusion handling, the WaEnhancer placeholder guard, the three
  release-note defects in `combine_logs()`, single-pair README marker substitution, cache re-keying
  and hyphenated cached versions. Added `tests/test_scraper_parsing.py`, the first coverage of the
  scraper parsing layer (APKMirror variant selection, unspecific-version handling, GitHub
  asset/version derivation, Direct link discovery), plus replays of both WhatsApp job failures,
  the mitigation-budget exhaustion and the patch-bundle collision. Added `tests/test_patcher.py`
  covering auto-patch detection and the MicroG opt-in, including the exact Xodo/Prime Video case.
  Suite: 57 -> 120 tests.

### Docs

- `README.md`: corrected the download-source order (fixed `direct` -> `github` -> `apkmirror` ->
  `uptodown`, cache first), the `parallel-jobs` default on CI, what `main.py clear` removes, the
  patch-retry budget and the `version` key semantics. Added Continuous Integration, Project
  Structure, Development & Testing and Limitations sections.
- `CONTRIBUTING.md`: replaced the claim that download sources are tried in the order their
  `*-dlurl` keys appear in the table — the order is fixed in `src/core/config.py`.

### Known limitations

- **A wildcard pin is a preference, not a guarantee.** When no source can supply a matching build the
  newest available one is published instead, relabelled from its manifest. That keeps a mirror alive,
  but for a pin that exists to satisfy an external module (WaEnhancer) it ships something unusable —
  which is why the WhatsApp entries are disabled rather than left green. Enforcing a pin strictly
  would need a new per-app option and a source carrying older versions.
- `build.yml` computes a `prerelease` flag from the patch bundles in use and passes it to the release
  step as `PRERELEASE`, but `gh release edit` never consumes it, so every release is published as a
  normal release. This is left as-is deliberately, and the reason is recorded next to the env var in
  `build.yml`: GitHub resolves `/releases/latest` to the newest **non**-pre-release release, and two
  things depend on that URL — `matrix.py::_fetch_our_releases()`, which would see no release and make
  the daily cron rebuild everything, and the Obtainium config of all 20 apps in `obtainium.json` and
  the README, which tracks `.../releases/latest` and would stop finding updates on every user's
  device. Wiring the flag up requires changing both first.
- `_version_from_cached_name()` recognises the architectures in `VALID_ARCHES`; a cache file written
  by an older revision with a different name shape falls back to cutting the version at the first
  hyphen.
- The relaxed second download pass re-fetches the artifact the strict pass discarded, so a
  wildcard miss costs one extra download of that APK.
