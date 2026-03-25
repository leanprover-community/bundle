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
| 5 | Full GUI test (VSCodium + lean4 extension + infoview) | **Not yet** |

## Future Work

### GUI Testing in CI

The remaining untested surface is the actual VSCodium GUI experience: does
the lean4 extension activate, does the infoview panel show proof state, do
diagnostics appear in the editor.

**Options** (in order of feasibility):

1. **`@vscode/test-electron`** (~3h effort)
   - Official VS Code testing framework
   - Launches Electron, runs tests with access to VS Code API
   - Can verify extension activation, language server status
   - Works on Windows CI without special display setup
   - Cannot easily test visual elements (infoview rendering)

2. **WebdriverIO + `wdio-vscode-service`** (~6h effort)
   - Full UI automation via Selenium/WebDriver
   - Can click buttons, verify text in panels, simulate user flow
   - Most comprehensive but most complex
   - Good for "open file, wait for diagnostics, check infoview" flow

3. **Playwright for Electron** (~4h effort)
   - Modern alternative to WebdriverIO
   - Can interact with Electron's Chromium layer
   - Less VS Code-specific tooling than WebdriverIO

**Recommendation**: Start with option 1 (`@vscode/test-electron`) as it's the
simplest and covers the most important case (extension activates + server connects).
Graduate to WebdriverIO only if we need to verify visual elements.

### Platform Support

- Linux bundles (launcher script exists, needs CI testing)
- macOS bundles (needs quarantine handling, `.app` bundle structure)

### Bundle Size Optimization

- Investigate whether `.ir` files can be omitted (saves ~30% of olean size)
- Investigate `.trace` file necessity
- Consider zstd compression instead of zip for smaller bundles
