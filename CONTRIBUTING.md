<div align="center">
<a href="#-development-setup"><img src="https://readme-typing-svg.demolab.com/?font=Google+Sans&size=25&pause=1000&color=4500FF&center=true&vCenter=true&random=false&width=600&lines=%F0%9F%9B%A0%EF%B8%8F+Contributing+to+apkforge;%E2%9A%99%EF%B8%8F+Setup+%C2%B7+Configuration+%C2%B7+Architecture"></a>

[![Build Status](https://img.shields.io/github/actions/workflow/status/softpyscho/apkforge/ci.yml?style=flat-square&logo=githubactions&logoColor=%23FFFFFF&label=Build%20Status&color=%234500FF)](https://github.com/softpyscho/apkforge/actions/workflows/ci.yml)   [![Python 3.13](https://img.shields.io/badge/Python-3.13+-4500FF?style=flat-square&logo=python&logoColor=%23FFFFFF)](https://www.python.org/downloads/)   [![Ruff](https://img.shields.io/badge/Lint-ruff-4500FF?style=flat-square&logo=ruff&logoColor=%23FFFFFF)](https://docs.astral.sh/ruff/)   [![Telegram](https://img.shields.io/badge/Telegram-Channel-4500FF?style=flat-square&logo=telegram&logoColor=%23FFFFFF)](https://t.me/apkforge)

A practical guide to setting up apkforge, running builds, configuring apps and contributing code.
</div>

## 📖 Contents

- [Development setup](#-development-setup)
- [Running builds](#-running-builds)
- [Configuration reference](#%EF%B8%8F-configuration-reference)
- [Adding an app or patch source](#-adding-an-app-or-patch-source)
- [Signing](#-signing)
- [Project layout](#-project-layout)
- [Testing & linting](#-testing--linting)
- [CI overview](#-ci-overview)
- [Pull requests](#-pull-requests)

## 💻 Development setup

**Requirements**

- [Git](https://git-scm.com/downloads)
- [Python 3.13+](https://www.python.org/downloads/)
- [uv](https://docs.astral.sh/uv/getting-started/installation/) — manages the virtualenv and dependencies
- [Java 21+](https://adoptium.net/temurin/releases/?version=21) — required by the Morphe CLI

**Install**

```bash
git clone --depth 1 https://github.com/softpyscho/apkforge.git
cd apkforge
uv sync
```

`uv` creates `.venv` and installs the locked dependencies automatically. No further setup is required.

## ▶️ Running builds

```bash
uv run main.py                    # build all enabled apps
uv run main.py SomeApp            # build a single app (by table name)
uv run main.py SomeApp arm64-v8a  # build with an architecture override
uv run main.py clear              # delete build/, temp/, build.md and build.json
```

| Output | Description |
|:-------|:------------|
| `build/` | Built `.apk` / `.apkm` artifacts |
| `build.json` | Machine-readable build report |
| `build.md` | Human-readable build log |
| `unmodified-apks/` | Cached stock APKs and sidecar `.src` / `.orig` metadata |
| `temp/` | Morphe CLI jars, patch bundles and scratch files |

## ⚙️ Configuration reference

All configuration lives in [`config.toml`](config.toml). Top-level keys are defaults inherited by every app entry; each app is a TOML table.

### Global keys

| 🔑 Key | 📝 Description | 🔤 Default |
|:------:|:--------------|:----------:|
| `parallel-jobs` | Number of builds to run concurrently | CPU count (2 on CI) |
| `brand` | Default brand used in output filenames | `Morphe` |
| `cli-version` | Morphe CLI version (`latest`, `dev`, or a specific tag) | `latest` |
| `cli-source` | CLI repository (`github:owner/repo` or `gitlab:owner/repo`) | `github:MorpheApp/morphe-desktop` |

### Per-app keys

| 🔑 Key | 📝 Description | 🔤 Default |
|:------:|:--------------|:----------:|
| `app-name` | Display name used in the filename and build label | table name with hyphens → spaces |
| `pkg-name` | Play Store package identifier (used for metadata + filenames) | fetched from source metadata |
| `brand` | Overrides the global `brand` for this app | global `brand` |
| `arch` | Target architecture: `all`, `both`, `arm64-v8a`, `armeabi-v7a`, `x86_64`, `x86` | `all` |
| `dpi` | Preferred screen density when a source offers variants | `""` (any) |
| `version` | `auto`, `latest`, a fixed version, or a wildcard like `2.26.30.xx` | `auto` |
| `changelog-keywords` | Rebuild this app only when these keywords appear in upstream notes | `[]` |
| `apkmirror-dlurl` | APKMirror page URL | `-` |
| `uptodown-dlurl` | Uptodown page URL | `-` |
| `direct-dlurl` | Direct download page or APK URL | `-` |
| `github-dlurl` | GitHub Releases page URL | `-` |
| `mirror` | Re-host the stock APK without patching | `false` |
| `keep-filename` | Mirrors: keep the source filename (sanitized for URLs) | `false` |
| `badge-color` | Hex colour for the README badge | `""` |
| `badge-icon` | simple-icons slug for the README badge | `""` |
| `exclusive-patches` | Apply only the patches listed in `[App.patches]` | `false` |
| `patcher-args` | Extra arguments passed directly to the Morphe CLI | `-` |
| `enabled` | Set to `false` to skip the entry | `true` |

### Patch table — `[AppName.patches]`

| Field | Description | Default |
|:-----:|:------------|:-------:|
| key | Patch source (`github:owner/repo` or `gitlab:owner/repo`) | — |
| `version` | Bundle version to fetch (`latest`, `dev`, or a tag) | `latest` |
| `include` | Patch names to apply (empty list = all defaults) | `[]` |
| `exclude` | Patch names to disable | `[]` |

Each `(source, version)` pair is fetched once and reused across every app that references it.

### Version selection

- `auto` — highest version supported by the **stable** patches.
- `latest` — highest version supported by patches, including experimental ones.
- `2.26.30.xx` — **wildcard**: newest available version matching that prefix, falling back to the newest overall if nothing matches.
- `1.2.3` — pinned: exactly that version, or a source-specific fallback when unavailable.

Value order matters: sources are tried in the order their `*-dlurl` keys appear in the table, after the local cache.

## ➕ Adding an app or patch source

No workflow changes are needed — the daily cron in `.github/workflows/ci.yml` scans `config.toml` and builds every enabled app automatically.

**Patched app** — add a table with download URLs and a `.patches` sub-table:

```toml
[SomeApp]
app-name = "Some App"
pkg-name = "com.example.someapp"
version = "auto"
arch = "arm64-v8a"
apkmirror-dlurl = "https://www.apkmirror.com/apk/inc/some-app/"
uptodown-dlurl = "https://some-app.en.uptodown.com/android"

[SomeApp.patches]
"github:owner/some-patches" = { version = "latest", include = ["Patch A"] }
```

**Unpatched mirror** — set `mirror = true` and provide only download URLs:

```toml
[SomeMirror]
app-name = "Some Mirror"
mirror = true
version = "latest"
arch = "arm64-v8a"
pkg-name = "com.example.mirror"
direct-dlurl = "https://example.com/downloads"
```

Validate your changes with:

```bash
uv run python -c "from src.core.config import load_toml, parse_config, parse_app_entries, CONFIG_PATH; d = load_toml(CONFIG_PATH); parse_app_entries(d, parse_config(d))"
```

## 🔑 Signing

APKs must be signed to install and update correctly. Create a `.env` file in the project root:

```env
KEYSTORE_BASE64=<base64-encoded keystore>
KEYSTORE_PASS=<keystore password>
KEYSTORE_ALIAS=<keystore alias>
```

Encode an existing keystore with `base64 -w 0 my.keystore`. On GitHub Actions, set the same names as repository secrets.

If no keystore is configured, a local `morphe.keystore` is used when present; otherwise the CLI's built-in debug keystore is used — which changes the signature on every CI run and makes app updates **impossible**.

> **Note:** apkforge previously verified downloaded stock APKs against a SHA-256 `sig.txt` list (`strict-sigcheck` / `skip-sigcheck`, `apksigner.jar`). That verification layer and all of its dependencies have been removed; only APK **signing** remains.

## 🗂️ Project layout

```
main.py                  # CLI entry point
src/core/
  builder.py             # orchestration: download → patch → optimize → sign
  config.py              # TOML parsing and validation
  network.py             # curl_cffi session, retries and per-domain locks
  patcher.py             # Morphe CLI wrapper (streaming output)
  prebuilts.py           # CLI jars and .mpp bundle fetching
  versions.py            # shared version-parsing helpers
  logger.py              # coloured / GitHub-annotation logging
src/scrapers/            # APKMirror, Uptodown, GitHub, Direct + base
src/scripts/             # CI helpers: matrix, logs, readme, telegram, wa_version
tests/                   # unittest suite
```

See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) for a deeper walkthrough.

## 🧪 Testing & linting

```bash
uv run python -m unittest discover -s tests -t . -v   # unit tests
uvx ruff@0.16.7 check .                               # lint
```

Both run in the **Lint, Test & Sync README** workflow on every push and pull request touching `src/`, `tests/`, `config.toml` or `pyproject.toml`.

## 🔁 CI overview

| Workflow | Trigger | Purpose |
|:---------|:--------|:--------|
| `ci.yml` | Daily cron + manual dispatch | Detects updates, then calls the reusable build workflow |
| `build.yml` | Reusable | Prepares a draft release, builds the matrix, merges artifacts, publishes |
| `lint.yml` | Push / PR to `src`, `tests`, config, pyproject | Runs ruff + tests and re-syncs the README |
| `cleanup.yml` | Weekly | Deletes prereleases older than 14 days |

A build only runs when an upstream patch source (or a mirrorable stock version) is newer than the last release, unless `force_build` is used.

## 🤝 Pull requests

- Keep changes focused and describe the motivation.
- Run `ruff` and the unit tests before opening a PR.
- AI-assisted contributions are welcome, but review every line you submit — you are responsible for it.
- By submitting a pull request you agree to license your contribution under the **GNU GPLv3**.

For bugs in the **build script**, use the [Script Bug Report](https://github.com/softpyscho/apkforge/issues/new?template=script.yml). For issues with a **built APK**, use the [Build Result Bug Report](https://github.com/softpyscho/apkforge/issues/new?template=build.yml). Feature ideas belong in [Discussions](https://github.com/softpyscho/apkforge/discussions).

---

<div align="center">
<i>Maintained with ❤️ by <a href="https://github.com/softpyscho">softpyscho</a></i>
</div>
