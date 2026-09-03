# Lean 4 Bundle Builder (Waterproof support)

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

## Quick start

Install the tool with [uv](https://docs.astral.sh/uv/):

```bash
uv tool install git+https://github.com/leanprover-community/bundle
```

That puts a `lean-bundle` CLI on your `PATH`. Build a bundle for your
current platform (auto-detected):

```bash
lean-bundle https://github.com/PatrickMassot/MDD154
```

The resulting `MDD154-bundle-<platform>.zip` lands in your current
directory. Send it to students; they unzip it and double-click
`Start_Lean`.

## Usage

Bundles must be built natively: run a Windows build on Windows, a macOS build
on macOS, and a Linux build on the matching Linux architecture.

```bash
lean-bundle https://github.com/PatrickMassot/MDD154 --platform windows
```

This produces `MDD154-bundle-windows.zip` containing:

- **VSCodium** (portable mode) with the lean4 extension pre-installed
- **Lean 4** toolchain (trimmed to essentials)
- A tiny **`git.exe` shim** (~10 KB) to satisfy the lean4 and built-in
  git extensions' startup probes without shipping a full git install
- The **project source files**
- **Only the oleans transitively needed** by the project (not all of Mathlib)

### Waterproof (Lean genre) projects

Projects built on [Waterproof](https://github.com/impermeable/waterproof-vscode)'s
Lean genre (`impermeable/waterproof-genre`, built on Verso) are just Lean 4
projects from this tool's point of view.

`lake build` resolves the genre
library like any other dependency, and Lean's dependency parser traces its
import closure the same as for Mathlib. The only extra step is bundling the
Waterproof extension itself:

```bash
lean-bundle https://github.com/your-org/your-waterproof-course --platform windows --waterproof
```

If a course deliberately contains Lean files that cannot compile, use
`--allow-unsolved`. The normal build is still attempted so dependencies used
by the exercises are materialized, but its expected failure is tolerated:

```bash
lean-bundle https://github.com/your-org/incomplete-waterproof-course \
    --waterproof \
    --allow-unsolved
```

Waterproof bundles follow the operating system's light or dark appearance.
Waterproof Light remains the fallback when no system preference is available.

The Waterproof extension is fetched from Open VSX
(`waterproof-tue.waterproof`). To bundle an unpublished build instead:

```bash
git clone https://github.com/impermeable/waterproof-vscode
cd waterproof-vscode && git lfs pull && npm ci
npm run package   # -> test_out/extension.vsix

lean-bundle https://github.com/your-org/your-waterproof-course \
    --platform windows \
    --waterproof-vsix waterproof-vscode/test_out/extension.vsix
```

### Proof-sheet bundle commands

Run these from the directory containing `bundle.py`. For bundles distributed
to students, use the pinned commands. For this example, we fix the proof sheets at commit
`e62b9166113d3f48b82a09bd5e728fbd779608cc`, VSCodium at `1.126.04524`, and
Waterproof at `0.12.0`.

**Pinned Windows:**

```powershell
python3 bundle.py https://github.com/impermeable/introduction-to-proof-sheets-lean --ref e62b9166113d3f48b82a09bd5e728fbd779608cc --platform windows --vscodium-version 1.126.04524 --waterproof-version 0.12.0 --allow-unsolved --work-dir "..\tmp\bewijzen-waterproof-windows" --clean-work-dir --output "..\bewijzen-waterproof-windows.zip"
```

**Pinned Linux x86-64:**

```bash
python3 bundle.py https://github.com/impermeable/introduction-to-proof-sheets-lean --ref e62b9166113d3f48b82a09bd5e728fbd779608cc --platform linux-x64 --vscodium-version 1.126.04524 --waterproof-version 0.12.0 --allow-unsolved --work-dir ../tmp/bewijzen-waterproof-linux-x64 --clean-work-dir --output ../bewijzen-waterproof-linux-x64.zip --open-file "Bewijzen/Lecture1/sheet1/_conjunction.lean"
```

The latest commands intentionally omit all three pins: they use the repository's
default branch and the latest VSCodium and Waterproof releases available when
the build starts.

**Latest Windows:**

```powershell
python3 bundle.py https://github.com/impermeable/introduction-to-proof-sheets-lean --platform windows --waterproof --allow-unsolved --work-dir "..\tmp\bewijzen-waterproof-windows-latest" --clean-work-dir --output "..\bewijzen-waterproof-windows-latest.zip"
```

**Latest Linux x86-64:**

```bash
python3 bundle.py https://github.com/impermeable/introduction-to-proof-sheets-lean --platform linux-x64 --waterproof --allow-unsolved --work-dir ../tmp/bewijzen-waterproof-linux-x64-latest --clean-work-dir --output ../bewijzen-waterproof-linux-x64-latest.zip --open-file "Bewijzen/Lecture1/sheet1/_conjunction.lean"
```

For ARM64 Linux, replace `linux-x64` with `linux-arm64` and adjust the output
names if desired. A work directory created by an older bundler has no ownership
marker; remove that directory manually once or choose a new path.

### Requirements (build machine only)

- Python 3.11+
- Git
- A project pinned to Lean 4.17+ (`--deps-json` accelerates Lean 4.22+)
- Network access (to download components and mathlib cache)
- The build host must match `--platform`; cross-platform builds are rejected.
- On Windows, the downloaded Lean toolchain's `leanc.exe` builds the small
  bundled `git.exe` shim; no additional C compiler is required.

Students need none of these.

### Options

````
--platform {windows,linux-x64,linux-arm64,darwin-x64,darwin-arm64}
    Native platform; must match the build host (default: auto-detect)

--output PATH
    Output zip file path

--project-dir PATH
    Use an already-cloned project instead of cloning fresh

--work-dir PATH
    Working directory for downloads and builds (default: a fresh temporary
    directory). Directories created by the bundler receive an ownership marker.

--clean-work-dir
    Remove the entire --work-dir before building instead of cleaning generated
    components individually. An existing directory must contain the valid
    ownership marker from a previous run. Unmarked directories and paths
    overlapping --project-dir or --waterproof-vsix are rejected.

--allow-unsolved
    Continue if the normal Lake build fails because exercises contain unsolved
    goals. Repository CI is responsible for catching other build failures.

--ref REF
    Git commit, branch, or tag to checkout

--vscodium-version VERSION
    Pin VSCodium version (default: latest)

--extension-version VERSION
    Pin lean4 extension version (default: latest)

--waterproof
    Bundle the Waterproof VS Code extension instead of the Lean 4 extension,
    for projects using the Waterproof Lean genre
    (impermeable/waterproof-genre, built on Verso).
    Only Waterproof's Lean path is wired up — it spawns `lake serve`
    itself via its own `waterproof.lakePath`/`waterproof.lakeArgs`
    settings, so no separate LSP setup is needed. The bundle pins
    `waterproof.skipLaunchChecks: "lean4"` in the project's workspace
    settings so Waterproof only starts the Lean language server. Rocq/
    coq-lsp is out of scope for this bundler: nothing opam-related is
    downloaded, built, or configured, and Rocq/`.v` support in the
    bundled Waterproof extension will not work.

    Fetched from Open VSX. To bundle an unpublished build, pass it via
    --waterproof-vsix.

--waterproof-version VERSION
    Pin the Waterproof extension version fetched from Open VSX (implies
    --waterproof; default: latest). Mutually exclusive with --waterproof-vsix.

--waterproof-vsix PATH
    Use an unpublished or locally-built Waterproof .vsix instead of downloading one
    (implies --waterproof). Waterproof's own release process (see
    CONTRIBUTING.md in impermeable/waterproof-vscode) is `npm run package`
    producing `test_out/extension.vsix`, uploaded directly to the VS Code
    Marketplace — there's no `.vsix` attached to GitHub releases. Build it
    with:
    ```
    git clone https://github.com/impermeable/waterproof-vscode
    cd waterproof-vscode && git lfs pull && npm ci
    npm run package   # -> test_out/extension.vsix
    ```
    then pass that path here.

--include [PATTERN ...]
    Additional file patterns to copy from the project (e.g. '*.json' 'data/')

--open-file NAME
    .lean file to auto-open on the first launch of an extracted bundle
    (default: no file; the workspace opens without an editor tab). Later
    launches restore the student's editor state. Not supported when combining
    --waterproof with --platform windows; see Known issues below.

--no-zip
    Assemble the bundle directory without creating a zip
````

### Example: local project checkout

If you've already cloned the project, you can use that checkout directly. The
bundler still fetches its dependencies and builds it:

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
  vscodium/                   # Portable VSCodium + selected editor extension
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
2. Downloads VSCodium portable and either the lean4 extension or, with
   `--waterproof`, the Waterproof extension
3. Uses batched `lean --deps-json` to compute the transitive import closure,
   with parallel `lean --src-deps` as a compatibility fallback
4. Copies only the needed modules' build artifacts (`.olean`, `.ilean`, etc.) into the bundle, skipping the thousands of Mathlib modules that aren't transitively imported
5. Trims the native Lean toolchain (removes clang and LLVM).
6. Creates a launcher script that sets `PATH`, `LEAN_PATH`, and
   `VSCODE_PORTABLE` (no `ELAN_HOME` — that would confuse the lean4
   extension's elan probing), and registers the bundled Lean in
   `~/.elan/toolchains/<encoded-name>/` (symlink on Unix, junction on
   Windows) so students with a prior elan install don't get a "Lean
   version is not installed" dialog
7. Packages everything into a zip

## Testing

Run the local Linux x86-64 test harness against an existing bundle:

```bash
./test.sh /path/to/MDD154-bundle
```

This runs the core unit tests, bundle structure verification, launcher tests, and
Playwright GUI tests (requires Xvfb). Build a bundle first with:

```bash
python bundle.py https://github.com/PatrickMassot/MDD154 --platform linux-x64 --no-zip --work-dir /tmp/bundle-local
./test.sh /tmp/bundle-local/MDD154-bundle
```

## Known issues

- **Opening a default Waterproof file on Windows.** Combining `--open-file`,
  `--waterproof`, and `--platform windows` is rejected. On a cold start, VS Code
  currently opens a file argument in its text editor instead of honoring
  `workbench.editorAssociations`; opening the file after startup uses the
  configured custom editor correctly. See
  [VS Code issue #325506](https://github.com/microsoft/vscode/issues/325506).
  Windows Waterproof bundles therefore open only the project workspace on first
  launch. Windows bundles using the regular Lean 4 extension are unaffected.

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
  _and_ VS Code's built-in git extension is disabled via settings.json
  — whichever probe comes last determines whether the shim stays.

- **Dep rewriting for offline use.** We rewrite `lake-manifest.json` and
  `lakefile.toml`/`lakefile.lean` to convert git dependencies to path
  dependencies so lake doesn't try to run git. This can be removed once lake
  supports an offline mode
  ([lean4#13101](https://github.com/leanprover/lean4/issues/13101)).

- **Writes to the student's `~/.elan/toolchains/`.** When the student
  launches the bundle and they already have elan installed, the launcher
  creates a symlink (Unix) or directory junction (Windows) at
  `~/.elan/toolchains/<encoded-name>/` pointing into the bundle, so
  elan reports the project's toolchain as installed. This is necessary
  because the lean4 VS Code extension unconditionally prepends
  `~/.elan/bin` to PATH during activation and queries elan; without our
  symlink, it pops a modal "Lean version is not installed" dialog.
  Side effect: if the student later deletes the bundle, they'll have a
  dangling symlink in `~/.elan/toolchains/` until they remove it by
  hand or via `elan toolchain uninstall`.

## Pinned versions

Several component versions are hardcoded and need periodic bumps:

| Component                                     | Where                                  | Notes                               |
| --------------------------------------------- | -------------------------------------- | ----------------------------------- |
| git shim version string                       | `shim/git_shim.c` (`VERSION_LINE`)     | Must be >= 2.0.0, not 2.25.x/2.26.x |
| even-better-toml extension                    | `download.py` `LEAN4_EXTENSION_DEPS`   | ID + version                        |
| elan installer                                | `.github/workflows/build-and-test.yml` | Tag in curl URL                     |
| GitHub Actions (checkout, setup-python, etc.) | `.github/workflows/build-and-test.yml` | Pinned by commit SHA                |

The **Lean toolchain** version comes from the target project's
`lean-toolchain` file and is not pinned here. **Waterproof** defaults to
latest and can be pinned via `--waterproof-version`, same as VSCodium and
the lean4 extension. **VSCodium** and the **lean4
extension** default to the latest release but can be pinned per-build via
`--vscodium-version` and `--extension-version`.

## License

Apache 2.0
