# Bundle Builder Roadmap

## Current State

The bundle builder creates self-contained offline Lean 4 bundles for Windows,
tested end-to-end in CI on a GitHub Actions Windows runner.

## CI Test Coverage

| Tier | What | Status |
|------|------|--------|
| 1 | Structural integrity (files, DLLs, path deps, settings, extension dir) | Done |
| 2 | `lake build --no-build` (olean completeness via lake, offline) | Done |
| 3 | LSP protocol test (language server responds to didOpen) | Done |
| 4 | Launcher script (environment variables correct) | Done |
| 5 | VSCodium integration smoke test (extension activation + diagnostics) | Done |

## Tier 5 Details

Tier 5 uses `@vscode/test-electron` to launch VSCodium with the bundled
extensions and run Mocha tests inside the extension host. The tests verify:

- **Extension activation**: lean4 extension is found and activates
- **Language server**: starts and produces diagnostics for `#check Nat.add`
- **Diagnostics correctness**: no error diagnostics in the fixture file
- **Settings**: `extensions.autoUpdate`, `update.mode`, `telemetry.telemetryLevel`
- **Environment**: PATH includes `lean/bin`, ELAN_HOME and LEAN_PATH are set

The tests run in CI on `windows-latest` and have also been validated on a
local Windows 11 VM (Incus) with the same 10/10 pass rate.

### Running the GUI tests locally

```bash
cd tests/gui
npm ci && npx tsc
BUNDLE_ROOT=/path/to/MDD154-bundle node out/run-tests.js
```

On Linux, use `xvfb-run` to provide a virtual display.

## Future Work

### Platform Support

- Linux bundles (launcher script exists, needs CI testing)
- macOS bundles (needs quarantine handling, `.app` bundle structure)

### Bundle Size Optimization

- Investigate whether `.ir` files can be omitted (saves ~30% of olean size)
- Investigate `.trace` file necessity
- Consider zstd compression instead of zip for smaller bundles
