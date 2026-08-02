# Security Policy

## Signing Model

apkforge signs every patched APK with a single project-wide keystore. The
keystore file (`morphe.keystore`) ships in the repository so that:

1. **Community forks can produce byte-identical signatures**, allowing users
   to switch between the official apkforge releases and a fork's releases
   without uninstalling/reinstalling.
2. **Anyone can verify a release's authenticity** by comparing its signing
   certificate fingerprint against the one published in the README:

   ```
   1894fee4df44d1823f3666db4743566d043dd72cbc13566433c1908270a4be10
   ```

The keystore password is **not** in the repository. This means:

- **Local builds** cannot sign with `morphe.keystore` — they fall back to
  the patcher's built-in debug keystore. APKs signed this way are **not**
  interchangeable with official releases.
- **Official CI builds** use the `KEYSTORE_BASE64`, `KEYSTORE_PASS`, and
  `KEYSTORE_ALIAS` repository secrets to sign with the production keystore.

## Threat Model

| Threat | Mitigation |
|:-------|:-----------|
| **Tampered stock APK from a mirror** | Every downloaded APK is verified against `sig.txt` (SHA-256 fingerprint) before patching. Set `strict-sigcheck = true` (default) to fail the build on missing entries. |
| **SSRF via malicious `config.toml`** | `NetworkManager` rejects non-HTTPS URLs (unless `allow_insecure = true`) and IP literals in loopback/private/link-local ranges. Override with `APKFORGE_ALLOW_PRIVATE_HOSTS=host1,host2`. |
| **Keystore password leak via `ps`/`/proc`** | Passwords are passed on the CLI for now (Morphe CLI doesn't yet support `--keystore-password-file`). They are redacted in all logs via `_SECRET_PATTERNS`. Self-hosted runners should restrict shell access. |
| **Cloud metadata exfiltration** | The SSRF guard rejects `169.254.169.254` and other link-local IPs. |
| **Replay of an old, vulnerable APK** | Each release is tagged with `YY.MM.DD-<release-group>` and the changelog includes the upstream patch version. Users can pin to a specific release via Obtainium. |
| **Compromised patch source** | Patch sources are pinned to specific tags in `config.toml`. The `version = "dev"` mode is opt-in per-app and clearly marked in the README. |

## Reporting a Vulnerability

If you discover a security issue:

1. **Do not open a public issue.**
2. Email the maintainer at the address listed on their GitHub profile
   ([softpsycho](https://github.com/softpsycho)).
3. Include a clear description and, if possible, a proof-of-concept.

You will receive an acknowledgment within 72 hours. Responsible disclosure
is appreciated — credit will be given in the release notes unless you
prefer to remain anonymous.

## `apksigner` Native Access

`PatcherCLI.check_signature` invokes apksigner with
`--enable-native-access=ALL-UNNAMED`. This grants the JVM access to native
libraries via JNI, which apksigner requires for its BouncyCastle native
crypto implementation. This is **not** a security concern because:

- apksigner is a well-known, signed artifact distributed by Google.
- The `bin/apksigner.jar` in this repo is pinned; changes to it require a
  PR review.
- The native access is scoped to the apksigner process only, not to the
  patcher or the build runner itself.

If you want to verify the apksigner jar's integrity, compare its SHA-256
against the one published in the Android SDK build-tools release notes.

## Self-Hosted Runners

If you run apkforge on a self-hosted GitHub Actions runner:

- Restrict shell access to trusted users — the keystore password is
  visible in `ps`/`/proc` while a build is running.
- Set `APKFORGE_INSECURE=0` (the default) to enforce HTTPS-only.
- Review the `APKFORGE_ALLOW_PRIVATE_HOSTS` env var if you need to fetch
  from an internal mirror.
- Use a dedicated runner — don't share it with untrusted workflows.
