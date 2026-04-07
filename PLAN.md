# Bundle Builder Roadmap

## Upstream changes that would simplify the bundle

- **Lake offline mode**
  ([lean4#13101](https://github.com/leanprover/lean4/issues/13101)).
  Would let us stop rewriting both `lake-manifest.json` and `lakefile.toml`/
  `lakefile.lean` to convert git deps to path deps, and fix the trace hash
  staleness that causes `lake build --no-build` to exit 3 after zip/unzip.

- **Extension git check suppression**
  ([Zulip discussion](https://leanprover.zulipchat.com/#narrow/channel/113488-general/topic/trylean.20bundle.20for.20lean4/near/581773347)).
  Would let us retire the Windows `git.exe` shim (`shim/git_shim.c`). Note
  that VS Code's built-in git extension *also* probes `git --version` and
  `git rev-parse --show-toplevel` at workspace activation, so even after
  the lean4 extension stops probing, the shim (or an explicit
  `"git.enabled": false` in `settings.json`) is still needed to keep the
  SCM sidebar quiet.

## Bundle size optimization

- Investigate whether `.ir` files can be omitted (saves ~30% of olean size)
- ~~Investigate `.trace` file necessity~~ — **not worth doing**
  ([#16](https://github.com/leanprover-community/bundle/issues/16)).
  Measurement on an MDD154 build: 7,803 `.trace` files, **2.09 MB total**
  (≈0.08% of the bundle). Deleting them does not just leave targets
  "stale" — `lake setup-file` immediately starts an 893-module rebuild
  instead of returning the import artifact JSON. The size win is
  negligible and the failure mode is the exact broken-infoview scenario
  [`test_lake_setup_file_offline`](tests/test_offline.py) guards against.
