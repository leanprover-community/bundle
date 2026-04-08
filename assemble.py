"""Assemble the bundle directory structure.

Takes the downloaded components and project files and arranges them
into the final bundle layout.
"""

import json
import os
import shutil
import subprocess
import time
from pathlib import Path



def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _touch_oleans(bundle_project: Path) -> None:
    """Touch all olean/ilean files so they are newer than .lean sources.

    Lake uses file modification times to decide whether a target is
    out-of-date. After copying files into the bundle (and especially
    after zip/unzip), timestamps can become equal or inverted, causing
    ``lake build --no-build`` to report targets as out-of-date.
    """
    import os
    # Set olean mtime to 1 second in the future to guarantee they're newer
    future = time.time() + 1
    for ext in ("*.olean", "*.ilean"):
        for p in bundle_project.rglob(ext):
            os.utime(p, (future, future))


_ALLOWLIST_FILES = {
    "lakefile.toml", "lakefile.lean", "lakefile",
    "lean-toolchain", "lake-manifest.json",
}
_ALLOWLIST_DIRS = {".vscode"}
_SKIP_DIRS = {".lake", ".git", ".github", "lake-packages"}


def copy_project_files(
    project_dir: Path,
    bundle_project: Path,
    extra_include: list[str] | None = None,
) -> None:
    """Copy the project's own source files into the bundle.

    Uses an allowlist: .lean files, lakefile configs, lean-toolchain,
    lake-manifest.json, and .vscode/. Use extra_include for additional
    glob patterns (e.g. ['*.json', 'data/'] for course data files).
    """
    for item in sorted(project_dir.iterdir()):
        if item.name in _SKIP_DIRS:
            continue
        dst = bundle_project / item.name
        if item.is_file():
            if item.name in _ALLOWLIST_FILES or item.suffix == ".lean":
                _copy_file(item, dst)
        elif item.is_dir():
            if item.name in _ALLOWLIST_DIRS:
                shutil.copytree(item, dst, dirs_exist_ok=True)
            else:
                # Recursively copy only .lean files from subdirectories
                for f in item.rglob("*.lean"):
                    rel = f.relative_to(project_dir)
                    if any(part in _SKIP_DIRS for part in rel.parts):
                        continue
                    _copy_file(f, bundle_project / rel)

    if extra_include:
        for pattern in extra_include:
            for match in project_dir.glob(pattern):
                rel = match.relative_to(project_dir)
                if any(part in _SKIP_DIRS for part in rel.parts):
                    continue
                dst = bundle_project / rel
                if match.is_file():
                    _copy_file(match, dst)
                elif match.is_dir():
                    shutil.copytree(
                        match, dst, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(*_SKIP_DIRS),
                    )


def prune_ir_from_bundle(bundle_project: Path) -> tuple[int, int]:
    """Delete Lean IR payloads from the bundle's ``.lake/`` tree.

    The student bundle only runs the LSP, which needs ``.olean`` / ``.ilean``
    / ``.olean.server`` / ``.olean.private`` to load modules. The Lean IR
    payloads (``*.ir`` files in ``build/lib/lean/``) are only needed for
    native code generation.

    Only the ``*.ir`` payloads are removed. Three other pieces are **kept**
    because deleting any of them makes Lake consider the target stale and
    rebuild it on the next ``lake setup-file``, which would break the
    Tier 2.5 "no rebuild in setup-file" guarantee:

    * ``*.ir.hash`` sidecars — Lake's trace check reads these to validate
      the recorded hash against the target's ``*.trace`` file.
    * The ``.lake/build/ir/`` directory as a whole — Lake validates the
      ``.c`` / ``.c.hash`` / ``.c.o.export`` / setup-json / trace files
      under it even though they are only used for native compilation.
    * The ``*.trace`` files themselves — rewriting them to drop the ``r``
      output breaks a different freshness invariant and also triggers
      rebuilds.

    Separately, ``lake setup-file`` still lists ``.ir`` paths in its
    ``importArts`` JSON and the Lean server reads them when started with
    ``--setup``. To prevent "file not found" errors at LSP startup,
    ``install_lake_wrapper`` replaces the ``lake`` binary with a wrapper
    that strips ``.ir`` entries from ``setup-file`` output.

    Returns ``(files_removed, bytes_freed)``.
    """
    files_removed = 0
    bytes_freed = 0

    for lib_dir in bundle_project.rglob(".lake/build/lib/lean"):
        if not lib_dir.is_dir():
            continue
        for p in lib_dir.rglob("*.ir"):
            if p.is_file() and not p.is_symlink():
                try:
                    bytes_freed += p.stat().st_size
                    p.unlink()
                    files_removed += 1
                except FileNotFoundError:
                    pass

    return files_removed, bytes_freed


_LAKE_WRAPPER_SH = r"""#!/bin/sh
# Lake wrapper: strips .ir entries from `lake setup-file` output so the
# Lean server doesn't try to read IR payloads that were pruned from the
# bundle. For all other subcommands, passes through unchanged.
REAL="$(dirname "$0")/lake.real"
if [ "$1" = "setup-file" ]; then
    "$REAL" "$@" | python3 -c "
import sys, json
raw = sys.stdin.buffer.read()
try:
    data = json.loads(raw)
    for arts in data.get('importArts', {}).values():
        arts[:] = [a for a in arts if not a.endswith('.ir')]
    json.dump(data, sys.stdout)
except Exception:
    sys.stdout.buffer.write(raw)
"
else
    exec "$REAL" "$@"
fi
"""


def install_lake_wrapper(bundle_dir: Path, platform: str) -> None:
    """Replace the bundle's ``lake`` binary with a wrapper that filters IR.

    The real binary is renamed to ``lake.real``; a shell wrapper takes its
    place and strips ``.ir`` entries from ``lake setup-file`` JSON output.
    Only supported on Unix; callers should skip this on Windows.
    """
    lake = bundle_dir / "lean" / "bin" / "lake"
    lake_real = bundle_dir / "lean" / "bin" / "lake.real"
    if lake.is_file():
        lake.rename(lake_real)
        lake.write_text(_LAKE_WRAPPER_SH)
        lake.chmod(0o755)


def copy_project_oleans(project_dir: Path, bundle_project: Path) -> int:
    """Copy the project's own build artifacts into the bundle.

    Copies .olean, .ilean, and .trace files so that Lake considers the
    project's modules up-to-date (lake setup-file won't try to rebuild).

    Returns the number of files copied.
    """
    build_dir = project_dir / ".lake" / "build" / "lib" / "lean"
    if not build_dir.is_dir():
        return 0

    count = 0
    bundle_build = bundle_project / ".lake" / "build" / "lib" / "lean"
    for f in build_dir.rglob("*"):
        if f.is_file() and f.suffix in (".olean", ".ilean", ".trace"):
            rel = f.relative_to(build_dir)
            _copy_file(f, bundle_build / rel)
            count += 1
    return count


def rewrite_manifest_to_path_deps(
    bundle_project: Path,
) -> None:
    """Rewrite lake-manifest.json to use local path deps instead of git deps.

    Lake's materializeDeps always runs git commands for git-type deps, even
    with --no-build. By rewriting the manifest to use path deps pointing at
    the local .lake/packages/ dirs, lake skips all git operations. This is
    a workaround until Lake supports an --offline flag
    (https://github.com/leanprover/lean4/issues/13101).
    """
    manifest_path = bundle_project / "lake-manifest.json"
    if not manifest_path.is_file():
        return

    manifest = json.loads(manifest_path.read_text())

    for pkg in manifest.get("packages", []):
        if pkg.get("type") == "git":
            pkg_name = pkg["name"]
            # Convert all git deps to path deps, even if the package
            # directory doesn't exist (build-time-only deps like Cli).
            # This prevents lake from trying any git operations.
            pkg["type"] = "path"
            pkg["dir"] = f".lake/packages/{pkg_name}"
            # Remove git-specific fields
            for key in ["url", "rev", "inputRev", "subDir"]:
                pkg.pop(key, None)

    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n")


def _rewrite_lakefile_toml_deps(bundle_project: Path) -> None:
    """Rewrite lakefile.toml git deps to path deps.

    Lake validates that the lakefile and manifest agree on dependency
    source kinds. If the manifest says path but the lakefile says git,
    Lake considers targets out-of-date and ``--no-build`` fails.
    """
    lakefile = bundle_project / "lakefile.toml"
    if not lakefile.is_file():
        return

    import tomllib
    text = lakefile.read_text()
    try:
        data = tomllib.loads(text)
    except Exception:
        return

    requires = data.get("require", [])
    if not requires:
        return

    # For each git require, replace the git line with a path line
    for req in requires:
        name = req.get("name", "")
        if "git" not in req:
            continue
        # Replace: git = "..." with path = ".lake/packages/<name>"
        # Also remove rev = "..." if present
        import re
        # Match the [[require]] block for this name and replace git/rev lines
        pattern = (
            r'(\[\[require\]\]\s*\n'
            r'name\s*=\s*"' + re.escape(name) + r'")\s*\n'
            r'git\s*=\s*"[^"]*"'
            r'(\s*\nrev\s*=\s*"[^"]*")?'
        )
        replacement = r'\1\npath = ".lake/packages/' + name + r'"'
        text = re.sub(pattern, replacement, text)

    lakefile.write_text(text)


def _rewrite_lakefile_lean_deps(bundle_project: Path) -> None:
    """Rewrite lakefile.lean git deps to path deps.

    Handles the Lean DSL syntax: ``require foo from git "url" @ "rev"``
    → ``require foo from ".lake/packages/foo"``
    """
    lakefile = bundle_project / "lakefile.lean"
    if not lakefile.is_file():
        return

    import re
    text = lakefile.read_text()
    # Match: require <name> from git "url" [@ "rev"]
    pattern = r'(require\s+(\w+)\s+)from\s+git\s+"[^"]*"(\s*@\s*"[^"]*")?'

    def replace_dep(m):
        prefix = m.group(1)
        name = m.group(2)
        return f'{prefix}from ".lake/packages/{name}"'

    new_text = re.sub(pattern, replace_dep, text)
    if new_text != text:
        lakefile.write_text(new_text)


def rewrite_deps_to_path(bundle_project: Path) -> None:
    """Rewrite all dependency references to path deps for offline use.

    Rewrites both the manifest and the lakefile so Lake sees consistent
    source kinds and doesn't consider targets out-of-date.
    """
    rewrite_manifest_to_path_deps(bundle_project)
    _rewrite_lakefile_toml_deps(bundle_project)
    _rewrite_lakefile_lean_deps(bundle_project)


def setup_vscodium_portable(
    vscodium_dir: Path,
    extension_dirs: list[Path],
    settings_template: Path,
) -> None:
    """Set up VSCodium in portable mode with extensions pre-installed.

    Args:
        vscodium_dir: Path to extracted VSCodium.
        extension_dirs: Paths to extracted extensions (lean4 + dependencies).
        settings_template: Path to settings.json template.
    """
    # Create portable data directory
    data_dir = vscodium_dir / "data"
    data_dir.mkdir(exist_ok=True)
    extensions_dir = data_dir / "extensions"

    registry = []

    for extension_dir in extension_dirs:
        # VSIX archives often contain their actual extension payload under
        # `extension/`; VSCodium expects package.json at the extension root.
        extension_root = extension_dir / "extension"
        if not extension_root.is_dir():
            extension_root = extension_dir

        # Install extension
        ext_dest = extensions_dir / extension_dir.name
        if ext_dest.exists():
            shutil.rmtree(ext_dest)
        shutil.copytree(extension_root, ext_dest)

        # Read extension metadata for the registry
        ext_package = ext_dest / "package.json"
        ext_version = "0.0.0"
        ext_publisher = "unknown"
        ext_name = extension_dir.name
        if ext_package.is_file():
            pkg = json.loads(ext_package.read_text())
            ext_version = pkg.get("version", ext_version)
            ext_publisher = pkg.get("publisher", ext_publisher)
            ext_name = pkg.get("name", ext_name)

        # Write extensions.json registry entry.
        # The location field with $mid is VS Code's internal URI format;
        # without it, VSCodium can't resolve the extension path.
        registry.append(
            {
                "identifier": {"id": f"{ext_publisher}.{ext_name}"},
                "version": ext_version,
                "location": {
                    "$mid": 1,
                    "path": f"/{extension_dir.name}",
                    "scheme": "file",
                },
                "relativeLocation": extension_dir.name,
                "metadata": {
                    "installedTimestamp": int(time.time() * 1000),
                },
            }
        )

    (extensions_dir / "extensions.json").write_text(json.dumps(registry, indent=2) + "\n")

    # Create user settings
    user_dir = data_dir / "user-data" / "User"
    user_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(settings_template, user_dir / "settings.json")


def assemble_bundle(
    project_dir: Path,
    lean_dir: Path,
    vscodium_dir: Path,
    extension_dirs: list[Path],
    git_shim_exe: Path | None,
    templates_dir: Path,
    bundle_dir: Path,
    platform: str,
    extra_include: list[str] | None = None,
) -> None:
    """Assemble the complete bundle directory.

    Args:
        project_dir: The built project directory (with .lake/packages/ and oleans).
        lean_dir: Extracted and trimmed lean toolchain directory.
        vscodium_dir: Extracted VSCodium directory.
        extension_dirs: Extracted extension directories (lean4 + dependencies).
        git_shim_exe: Path to the built git.exe shim (Windows only, None
            on other platforms).
        templates_dir: Directory containing launcher and settings templates.
        bundle_dir: Output bundle directory to create.
        platform: Target platform key.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)

    print("Copying lean toolchain...")
    shutil.copytree(lean_dir, bundle_dir / "lean", symlinks=True, dirs_exist_ok=True)

    if git_shim_exe is not None:
        # Place the shim at <bundle>/git/cmd/git.exe so start_lean.cmd can
        # continue prepending "%BUNDLE_ROOT%\git\cmd" to PATH. The layout
        # matches the old MinGit install, which keeps the launcher script
        # and any muscle-memory docs working.
        print("Installing git shim...")
        git_cmd = bundle_dir / "git" / "cmd"
        git_cmd.mkdir(parents=True, exist_ok=True)
        shutil.copy2(git_shim_exe, git_cmd / "git.exe")

    print("Setting up VSCodium...")
    shutil.copytree(vscodium_dir, bundle_dir / "vscodium", symlinks=True, dirs_exist_ok=True)
    setup_vscodium_portable(
        bundle_dir / "vscodium",
        extension_dirs,
        templates_dir / "settings.json",
    )

    print("Copying project files...")
    bundle_project = bundle_dir / "project"
    copy_project_files(project_dir, bundle_project, extra_include=extra_include)

    print("Copying project oleans...")
    n_proj = copy_project_oleans(project_dir, bundle_project)
    print(f"  {n_proj} project olean files copied")

    # Copy the full .lake/ directory from the source project.
    # Lake's trace validation depends on the complete build artifact tree —
    # partial copies (even of build/lib/lean/ + build/ir/) cause hash
    # mismatches that trigger full rebuilds (>600s). IR artifacts are
    # removed below after the in-bundle rebuild has updated the traces.
    print("Copying project .lake directory...")
    src_lake = project_dir / ".lake"
    dst_lake = bundle_project / ".lake"
    if src_lake.is_dir():
        shutil.copytree(src_lake, dst_lake, symlinks=True, dirs_exist_ok=True)
        n_files = sum(1 for _ in dst_lake.rglob("*") if _.is_file())
        print(f"  {n_files} files copied")

    print("Rewriting deps to path deps...")
    rewrite_deps_to_path(bundle_project)

    # Ensure oleans are strictly newer than sources so Lake's timestamp
    # check doesn't consider them out-of-date after copy/zip/unzip.
    print("Fixing olean timestamps...")
    _touch_oleans(bundle_project)

    # Rebuild the project's own modules inside the assembled bundle.
    # This ensures the project's build traces are valid for the bundle's
    # workspace configuration. Only the project's own modules need
    # recompilation (~seconds); dependency oleans are already cached.
    # Cross-compiled bundles (e.g. macOS built on Linux) can't run lake here;
    # the test jobs handle that case by running lake setup-file on the target.
    print("Rebuilding project modules with rewritten manifest...")
    lake_bin = bundle_dir / "lean" / "bin" / "lake"
    can_run = False
    if lake_bin.is_file():
        try:
            subprocess.run(
                [str(lake_bin), "--version"],
                capture_output=True, timeout=10,
            )
            can_run = True
        except (OSError, subprocess.TimeoutExpired):
            pass
    if can_run:
        rebuild_env = os.environ.copy()
        rebuild_env["PATH"] = str(bundle_dir / "lean" / "bin") + os.pathsep + rebuild_env.get("PATH", "")
        rebuild_env["ELAN_HOME"] = str(bundle_dir / "lean")
        try:
            result = subprocess.run(
                [str(lake_bin), "build"],
                cwd=str(bundle_project),
                env=rebuild_env,
                capture_output=True,
                timeout=600,
            )
            if result.returncode == 0:
                print("  Project rebuild successful")
            else:
                stderr = result.stderr.decode("utf-8", errors="replace")[:500]
                print(f"  Warning: project rebuild exited {result.returncode}: {stderr}")
        except subprocess.TimeoutExpired:
            print("  Warning: project rebuild timed out (600s), continuing without rebuild")
    else:
        print("  Skipped (cross-platform build, lake binary not runnable)")

    # Strip Lean IR payloads from the .lake build tree. The LSP does not
    # need them, and we can safely drop them without breaking Lake's trace
    # freshness check (see prune_ir_from_bundle for why the .ir.hash
    # sidecars, build/ir/, and .trace files all stay put).
    # On Windows the lake wrapper mechanism is unreliable (the VS Code
    # extension may invoke lake.exe directly, bypassing .cmd wrappers),
    # so we skip IR pruning there.
    if not platform.startswith("windows"):
        print("Pruning Lean IR payloads from bundle...")
        n_pruned, bytes_freed = prune_ir_from_bundle(bundle_project)
        print(f"  {n_pruned} *.ir files removed ({bytes_freed / (1024 * 1024):.1f} MB freed)")
        if n_pruned > 0:
            print("Installing lake wrapper to strip .ir from setup-file output...")
            install_lake_wrapper(bundle_dir, platform)
    else:
        print("Skipping IR pruning on Windows (lake wrapper not supported)")

    print("Installing launcher...")
    if platform.startswith("windows"):
        launcher_src = templates_dir / "start_lean.cmd"
        launcher_dst = bundle_dir / "Start_Lean.cmd"
    else:
        launcher_src = templates_dir / "start_lean.sh"
        launcher_dst = bundle_dir / "Start_Lean.sh"

    shutil.copy2(launcher_src, launcher_dst)
    if not platform.startswith("windows"):
        launcher_dst.chmod(0o755)

    print(f"Bundle assembled at: {bundle_dir}")
