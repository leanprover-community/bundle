# Bundle Builder Roadmap

## Not yet tested

- **Offline guarantee.** ✅ `tests/test_offline.py` runs Lake and the LSP
  server inside `unshare -rn` (Linux network namespace) to verify zero network
  access. CI builds a Linux bundle and runs these tests with a preflight check
  that fails (not skips) if namespace isolation is unavailable.

- **Infoview panel rendering.** We verify the language server produces
  diagnostics, but don't test that the infoview webview actually renders proof
  state. Would require Playwright for Electron or WebdriverIO UI automation.

- **User interaction flow.** Clicking through files, typing in the editor,
  seeing live feedback. Tests currently use the VS Code API programmatically.

- **macOS bundles.** Needs quarantine handling and `.app` bundle structure.

- **Linux CI.** ✅ The `test-linux-offline` job builds a Linux bundle, verifies
  its structure, and runs the offline guarantee tests.

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
