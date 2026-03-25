# Bundle Builder Roadmap

## Not yet tested

- **Infoview panel rendering.** We verify the language server produces
  diagnostics, but don't test that the infoview webview actually renders proof
  state. Would require Playwright for Electron or WebdriverIO UI automation.

- **User interaction flow.** Clicking through files, typing in the editor,
  seeing live feedback. Tests currently use the VS Code API programmatically.

- **First launch experience.** The launcher script sets the right environment,
  but we don't test that double-clicking "Start Lean.cmd" actually opens
  VSCodium in the correct folder.

- **Offline guarantee.** We don't run tests with network disabled. Lake could
  theoretically try to fetch something we missed.

- **macOS bundles.** Needs quarantine handling and `.app` bundle structure.

- **Linux CI.** Linux bundles work (tested locally with xvfb) but aren't in CI.

## Upstream changes that would simplify the bundle

- **Lake offline mode**
  ([lean4#13101](https://github.com/leanprover/lean4/issues/13101)).
  Would let us stop rewriting `lake-manifest.json` to convert git deps to path
  deps.

- **Extension git check suppression**
  ([Zulip discussion](https://leanprover.zulipchat.com/#narrow/channel/113488-general/topic/trylean.20bundle.20for.20lean4/near/581773347)).
  Would let us drop MinGit (~46MB) from the bundle entirely.

## Bundle size optimization

- Investigate whether `.ir` files can be omitted (saves ~30% of olean size)
- Investigate `.trace` file necessity
- Consider zstd compression instead of zip for smaller bundles
