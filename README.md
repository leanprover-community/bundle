# Lean 4 Bundle Builder

Create self-contained, offline Lean 4 bundles for teaching.

Students download a zip, unpack it, double-click "Start Lean", and get a
working editor with no installation, no network access, and no command line
needed.

## Usage

```bash
python bundle.py https://github.com/PatrickMassot/MDD154 --platform windows-x64
```

This produces `MDD154-bundle-windows-x64.zip` containing:

- **VSCodium** (portable mode) with the lean4 extension pre-installed
- **Lean 4** toolchain (trimmed to essentials)
- **MinGit** (minimal git for Windows, needed by the lean4 extension)
- The **project source files**
- **Only the oleans transitively needed** by the project (not all of Mathlib)

### Requirements (build machine only)

- Python 3.11+
- Git
- [elan](https://github.com/leanprover/elan) with the project's Lean toolchain
- Lean 4.17+ (for `lean --src-deps`)
- Network access (to download components and mathlib cache)

Students need none of these.

### Options

```
--platform {windows-x64,linux-x64,darwin-x64,darwin-arm64}
    Target platform (default: auto-detect)

--output PATH
    Output zip file path

--project-dir PATH
    Use an already-cloned and built project instead of cloning fresh

--work-dir PATH
    Working directory for downloads (default: temp dir)

--vscodium-version VERSION
    Pin VSCodium version (default: latest)

--extension-version VERSION
    Pin lean4 extension version (default: latest)

--no-zip
    Assemble the bundle directory without creating a zip
```

### Example: pre-built project

If you've already cloned and built the project:

```bash
python bundle.py https://github.com/PatrickMassot/MDD154 \
    --project-dir /path/to/MDD154 \
    --platform windows-x64
```

## What's in the bundle

```
MDD154-bundle/
  Start Lean.cmd              # Double-click to launch (Windows)
  lean/                       # Trimmed Lean toolchain
  vscodium/                   # Portable VSCodium + lean4 extension
  project/                    # Course project
    lakefile.toml
    lean-toolchain
    Mdd154/*.lean             # Student exercises
    .lake/
      build/lib/lean/         # Project oleans
      packages/               # Pruned dependency oleans + sources
```

## How it works

1. Clones the target project and builds it (fetching mathlib cache)
2. Parses all `import` statements to compute the transitive closure of needed modules
3. Copies only those modules' `.olean` and `.lean` files into the bundle
4. Downloads VSCodium portable and the lean4 extension
5. Trims the Lean toolchain (removes clang, LLVM, ~500MB saved)
6. Creates a launcher script that sets PATH, LEAN_PATH, ELAN_HOME
7. Packages everything into a zip

## Known issues

- **MinGit is bundled as a workaround.** The lean4 VS Code extension checks
  for git at startup and blocks the language server if it's missing. We bundle
  MinGit (~46MB) to satisfy this check, even though the bundle doesn't need git
  at runtime. This can be removed once the extension provides a way to suppress
  the dependency check
  ([Zulip discussion](https://leanprover.zulipchat.com/#narrow/channel/113488-general/topic/trylean.20bundle.20for.20lean4/near/581773347)).

- **Manifest rewriting.** We rewrite `lake-manifest.json` to convert git
  dependencies to path dependencies so lake doesn't try to run git. This can be
  removed once lake supports an offline mode
  ([lean4#13101](https://github.com/leanprover/lean4/issues/13101)).

## Pinned versions

Several component versions are hardcoded and need periodic bumps:

| Component | Where | Notes |
|-----------|-------|-------|
| MinGit | `download.py` `MINGIT_VERSION` | Windows only |
| even-better-toml extension | `download.py` `ALLOWED_EXTENSION_DEPS` | ID + version |
| elan installer | `.github/workflows/build-and-test.yml` | Tag in curl URL |
| GitHub Actions (checkout, setup-python, etc.) | `.github/workflows/build-and-test.yml` | Pinned by commit SHA |

The **Lean toolchain** version comes from the target project's
`lean-toolchain` file and is not pinned here. **VSCodium** and the **lean4
extension** default to the latest release but can be pinned per-build via
`--vscodium-version` and `--extension-version`.

## License

Apache 2.0
