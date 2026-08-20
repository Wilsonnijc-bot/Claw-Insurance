# CDP Helper packaging

The host CDP helper is intentionally outside Docker because it controls the
customer's Chrome/Chromium process and reuses the host WhatsApp Web profile.
Customer releases should ship it as a standalone executable so Python is not a
runtime prerequisite.

## Build model

PyInstaller does not cross-compile. Build the helper once on every supported
host/CPU combination:

- Windows x86-64
- macOS x86-64
- macOS arm64
- Linux x86-64
- Linux arm64 when Linux ARM desktop support is required

Run `scripts/build-cdp-helper.ps1` on Windows or
`scripts/build-cdp-helper.sh` on macOS/Linux. The output is
`dist/cdp-helper/nanobot-cdp-helper[.exe]`.

The release packaging job should place the correct binary at:

```text
cdp-helper/nanobot-cdp-helper.exe   # Windows release bundle
cdp-helper/nanobot-cdp-helper       # macOS/Linux release bundle
```

The customer setup scripts prefer this executable and fall back to the
source/Python installer only in a developer checkout.

## Production signing

Before public distribution:

1. Sign the Windows executable and installer with an Authenticode certificate.
2. Sign and notarize both macOS binaries with an Apple Developer ID.
3. Publish SHA-256 checksums with every release.
4. Build on controlled CI runners and retain the build provenance.
5. Package the Windows executable with Inno Setup/MSIX and the macOS executable
   in a signed PKG/DMG only after the standalone install/health flow passes.

The standalone executable supports:

```text
nanobot-cdp-helper install --project-root <release-directory>
nanobot-cdp-helper health
nanobot-cdp-helper serve
```
