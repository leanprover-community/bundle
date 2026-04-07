# Bundle Builder Roadmap

## Upstream changes that would simplify the bundle

- **Lake offline mode**
  ([lean4#13101](https://github.com/leanprover/lean4/issues/13101)).
  Would let us stop rewriting both `lake-manifest.json` and `lakefile.toml`/
  `lakefile.lean` to convert git deps to path deps, and fix the trace hash
  staleness that causes `lake build --no-build` to exit 3 after zip/unzip.

- **Extension git check suppression**
  ([Zulip discussion](https://leanprover.zulipchat.com/#narrow/channel/113488-general/topic/trylean.20bundle.20for.20lean4/near/581773347)).
  Would let us retire the Windows `git.exe` shim (`shim/git_shim.c`).
  The shim is already in place, but the lean4 extension still probes for
  git at startup. Note that VS Code's built-in git extension *also* probes
  `git --version` and `git rev-parse --show-toplevel` at workspace
  activation, so even after the lean4 extension stops probing, the shim
  (or an explicit `"git.enabled": false` in `settings.json`) is still
  needed to keep the SCM sidebar quiet.
