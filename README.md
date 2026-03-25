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

## License

Apache 2.0
