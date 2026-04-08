# Lean 4 Bundle Builder

Create self-contained, offline Lean 4 bundles for teaching.

Students download a zip, unpack it, double-click "Start_Lean", and get a
working editor with no installation, no network access, and no command line
needed.

## What it looks like

CI runs Playwright GUI smoke tests on all supported platforms and publishes
screenshots to [GitHub Pages](https://leanprover-community.github.io/bundle/).

<table>
<tr><th></th><th>Linux x64</th><th>Linux arm64</th><th>macOS</th><th>Windows</th></tr>
<tr>
<td><strong>Infoview</strong></td>
<td><a href="https://leanprover-community.github.io/bundle/linux/infoview-goals.png"><img src="https://leanprover-community.github.io/bundle/linux/infoview-goals.png" width="200" alt="Linux x64 infoview"></a></td>
<td><a href="https://leanprover-community.github.io/bundle/linux-arm64/infoview-goals.png"><img src="https://leanprover-community.github.io/bundle/linux-arm64/infoview-goals.png" width="200" alt="Linux arm64 infoview"></a></td>
<td><a href="https://leanprover-community.github.io/bundle/macos/infoview-goals.png"><img src="https://leanprover-community.github.io/bundle/macos/infoview-goals.png" width="200" alt="macOS infoview"></a></td>
<td><a href="https://leanprover-community.github.io/bundle/windows/infoview-goals.png"><img src="https://leanprover-community.github.io/bundle/windows/infoview-goals.png" width="200" alt="Windows infoview"></a></td>
</tr>
<tr>
<td><strong>Diagnostics</strong></td>
<td><a href="https://leanprover-community.github.io/bundle/linux/interaction-error.png"><img src="https://leanprover-community.github.io/bundle/linux/interaction-error.png" width="200" alt="Linux x64 diagnostics"></a></td>
<td><a href="https://leanprover-community.github.io/bundle/linux-arm64/interaction-error.png"><img src="https://leanprover-community.github.io/bundle/linux-arm64/interaction-error.png" width="200" alt="Linux arm64 diagnostics"></a></td>
<td><a href="https://leanprover-community.github.io/bundle/macos/interaction-error.png"><img src="https://leanprover-community.github.io/bundle/macos/interaction-error.png" width="200" alt="macOS diagnostics"></a></td>
<td><a href="https://leanprover-community.github.io/bundle/windows/interaction-error.png"><img src="https://leanprover-community.github.io/bundle/windows/interaction-error.png" width="200" alt="Windows diagnostics"></a></td>
</tr>
<tr>
<td><strong>Project</strong></td>
<td><a href="https://leanprover-community.github.io/bundle/linux/project-exercise.png"><img src="https://leanprover-community.github.io/bundle/linux/project-exercise.png" width="200" alt="Linux x64 project"></a></td>
<td><a href="https://leanprover-community.github.io/bundle/linux-arm64/project-exercise.png"><img src="https://leanprover-community.github.io/bundle/linux-arm64/project-exercise.png" width="200" alt="Linux arm64 project"></a></td>
<td><a href="https://leanprover-community.github.io/bundle/macos/project-exercise.png"><img src="https://leanprover-community.github.io/bundle/macos/project-exercise.png" width="200" alt="macOS project"></a></td>
<td><a href="https://leanprover-community.github.io/bundle/windows/project-exercise.png"><img src="https://leanprover-community.github.io/bundle/windows/project-exercise.png" width="200" alt="Windows project"></a></td>
</tr>
</table>

## Usage

```bash
python bundle.py https://github.com/PatrickMassot/MDD154 --platform windows
```

This produces `MDD154-bundle-windows.zip` containing:

- **VSCodium** (portable mode) with the lean4 extension pre-installed
- **Lean 4** toolchain (trimmed to essentials)
- A tiny **`git.exe` shim** (~10 KB) to satisfy the lean4 and built-in
  git extensions' startup probes without shipping a full git install
- The **project source files**
- **Only the oleans transitively needed** by the project (not all of Mathlib)

### Requirements (build machine only)

- Python 3.11+
- Git
- [elan](https://github.com/leanprover/elan) with the project's Lean toolchain
- Lean 4.17+ (for `lean --src-deps`)
- Network access (to download components and mathlib cache)
- A C compiler able to produce 64-bit Windows PE binaries **when
  building Windows bundles** (mingw-w64 is recommended;
  `apt-get install gcc-mingw-w64-x86-64` on Debian/Ubuntu). Zig or native
  `gcc`/`clang` on a Windows build host also work.

Students need none of these.

### Options

```
--platform {windows,linux-x64,linux-arm64,darwin-x64,darwin-arm64}
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
    --platform windows
```

## What's in the bundle

```
MDD154-bundle/
  Start_Lean.command/.cmd/.sh  # Double-click to launch (one per platform)
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

## Testing

Run all tests locally against an existing bundle:

```bash
./test.sh /path/to/MDD154-bundle
```

This runs unit tests, bundle structure verification, launcher tests, and
Playwright GUI tests (requires Xvfb). Build a bundle first with:

```bash
python bundle.py https://github.com/PatrickMassot/MDD154 --platform linux-x64 --no-zip --work-dir /tmp/bundle-local
./test.sh /tmp/bundle-local/MDD154-bundle
```

## Known issues

- **Git shim on Windows.** The lean4 VS Code extension and VS Code's
  built-in git extension both probe for `git` on PATH at startup. Rather
  than shipping the full 46 MB MinGit distribution, the bundle includes a
  ~10 KB C shim at `git/cmd/git.exe` that answers only the two probes
  both extensions make at activation: `git --version` (returns
  `git version 2.47.0`) and `git rev-parse --show-toplevel` (returns
  "not a git repository"). Lake's own git calls are optional fallbacks
  (`captureProc?`/`testProc`) and also tolerate the shim's non-zero
  exits. Source: `shim/git_shim.c`. This can be retired entirely once
  the lean4 extension provides a way to suppress its git check
  ([Zulip discussion](https://leanprover.zulipchat.com/#narrow/channel/113488-general/topic/trylean.20bundle.20for.20lean4/near/581773347))
  *and* VS Code's built-in git extension is disabled via settings.json
  — whichever probe comes last determines whether the shim stays.

- **Dep rewriting for offline use.** We rewrite `lake-manifest.json` and
  `lakefile.toml`/`lakefile.lean` to convert git dependencies to path
  dependencies so lake doesn't try to run git. This can be removed once lake
  supports an offline mode
  ([lean4#13101](https://github.com/leanprover/lean4/issues/13101)).

## Pinned versions

Several component versions are hardcoded and need periodic bumps:

| Component | Where | Notes |
|-----------|-------|-------|
| git shim version string | `shim/git_shim.c` (`VERSION_LINE`) | Must be >= 2.0.0, not 2.25.x/2.26.x |
| even-better-toml extension | `download.py` `ALLOWED_EXTENSION_DEPS` | ID + version |
| elan installer | `.github/workflows/build-and-test.yml` | Tag in curl URL |
| GitHub Actions (checkout, setup-python, etc.) | `.github/workflows/build-and-test.yml` | Pinned by commit SHA |

The **Lean toolchain** version comes from the target project's
`lean-toolchain` file and is not pinned here. **VSCodium** and the **lean4
extension** default to the latest release but can be pinned per-build via
`--vscodium-version` and `--extension-version`.

## License

Apache 2.0
