# Bundle Builder Roadmap

## Not yet tested

- **Infoview panel rendering.** We verify the language server produces
  diagnostics, but don't test that the infoview webview actually renders proof
  state. Would require Playwright for Electron or WebdriverIO UI automation.

- **User interaction flow.** Clicking through files, typing in the editor,
  seeing live feedback. Tests currently use the VS Code API programmatically.

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
- **Git shim instead of MinGit (Windows, saves ~46 MB).** The lean4 VS Code
  extension checks for `git` on PATH at startup and blocks the language server
  if it's missing. But Lake's git operations are already neutralized by
  rewriting manifests to path deps, so no real git functionality is needed at
  runtime. A tiny shim binary (a few KB) that responds to the extension's
  probe commands (`git --version`, `git rev-parse`, etc.) with plausible
  canned responses could replace the full 46 MB MinGit. This is roughly a
  **15–20% reduction** in total bundle size (MinGit is ~46 MB out of a
  ~250–300 MB bundle). Unlike the upstream suppression approach, this
  requires no extension changes and can be implemented immediately.
