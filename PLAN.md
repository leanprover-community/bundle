# Bundle Builder Roadmap

## Not yet tested

- **Infoview panel rendering.** We verify the language server produces
  diagnostics, but don't test that the infoview webview actually renders proof
  state. Would require Playwright for Electron or WebdriverIO UI automation.

- **User interaction flow.** Clicking through files, typing in the editor,
  seeing live feedback. Tests currently use the VS Code API programmatically.

- **macOS offline testing.** macOS has no unprivileged network namespace
  equivalent to Linux's `unshare -rn`. The offline guarantee is proven on
  Linux; macOS tests run Tiers 1–4 without network isolation.

- **macOS Tier 5 (VSCodium GUI).** The `.app` bundle's framework
  symlinks are not preserved through Python's `zipfile` round-trip,
  causing `dyld` errors when launching Electron. Need to preserve
  symlinks in `create_zip` (e.g. use `ditto` on macOS or store
  symlinks in the zip).

## Upstream changes that would simplify the bundle

- **Lake offline mode**
  ([lean4#13101](https://github.com/leanprover/lean4/issues/13101)).
  Would let us stop rewriting both `lake-manifest.json` and `lakefile.toml`/
  `lakefile.lean` to convert git deps to path deps, and fix the trace hash
  staleness that causes `lake build --no-build` to exit 3 after zip/unzip.

- **Extension git check suppression**
  ([Zulip discussion](https://leanprover.zulipchat.com/#narrow/channel/113488-general/topic/trylean.20bundle.20for.20lean4/near/581773347)).
  Would let us drop MinGit (~46MB) from the bundle entirely.

## Bundle size optimization

- Investigate whether `.ir` files can be omitted (saves ~30% of olean size)
- Investigate `.trace` file necessity
- Consider zstd compression instead of zip for smaller bundles
