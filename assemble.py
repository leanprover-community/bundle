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



_BUILD_MARKERS = (
    ("build", "lib", "lean"),
    ("build", "ir"),
)


def _module_stem_from_build_path(rel_parts: tuple[str, ...]) -> str | None:
    """Extract module stem from a ``.lake/``-relative path if it is a build artifact.

    Returns the module stem (e.g. ``"Mathlib/Algebra/Group/Basic"``) for
    files under ``build/lib/lean/`` or ``build/ir/`` directories, or
    ``None`` if the path is not inside a recognised build artifact tree.
    """
    for marker in _BUILD_MARKERS:
        mlen = len(marker)
        for i in range(len(rel_parts) - mlen):
            if rel_parts[i : i + mlen] == marker:
                after = rel_parts[i + mlen :]
                if not after:
                    return None
                # Strip *all* extensions from the filename to recover the
                # module name component.  E.g. "Basic.olean.private" → "Basic".
                filename = after[-1]
                dot = filename.find(".")
                base = filename[:dot] if dot > 0 else filename
                if len(after) > 1:
                    return str(Path(*after[:-1]) / base)
                return base
    return None


def copy_lake_selective(
    src_lake: Path,
    dst_lake: Path,
    needed_stems: set[str] | None,
) -> tuple[int, int]:
    """Copy ``.lake/`` directory, optionally pruning to the import closure.

    When *needed_stems* is given, build artifacts under ``build/lib/lean/``
    and ``build/ir/`` are copied only for modules whose stem is in the set.
    All other files (config, lakefiles, sources, etc.) are always copied.

    Returns ``(files_copied, files_skipped)``.
    """
    if needed_stems is None:
        shutil.copytree(src_lake, dst_lake, symlinks=True, dirs_exist_ok=True)
        n = sum(1 for p in dst_lake.rglob("*") if p.is_file() or p.is_symlink())
        return n, 0

    # Pre-compute the set of directory prefixes that contain at least one
    # needed module so we can skip entire sub-trees early.
    needed_prefixes: set[str] = set()
    for s in needed_stems:
        parts = s.split("/")
        for i in range(1, len(parts) + 1):
            needed_prefixes.add("/".join(parts[:i]))

    skipped = [0]

    def _ignore(directory: str, contents: list[str]) -> set[str]:
        ignored: set[str] = set()
        dir_path = Path(directory)
        try:
            rel_dir = dir_path.relative_to(src_lake)
        except ValueError:
            return ignored
        dir_parts = tuple(rel_dir.parts)

        for name in contents:
            full = dir_path / name
            entry_parts = dir_parts + (name,)

            if full.is_dir() and not full.is_symlink():
                # For directories inside build artifact trees, skip the
                # entire sub-tree when no needed stem has a matching prefix.
                stem = _module_stem_from_build_path(entry_parts)
                if stem is not None and stem not in needed_prefixes:
                    ignored.add(name)
                    skipped[0] += 1
                continue

            stem = _module_stem_from_build_path(entry_parts)
            if stem is not None and stem not in needed_stems:
                ignored.add(name)
                skipped[0] += 1

        return ignored

    shutil.copytree(
        src_lake, dst_lake, symlinks=True, dirs_exist_ok=True, ignore=_ignore,
    )
    n_copied = sum(1 for p in dst_lake.rglob("*") if p.is_file() or p.is_symlink())
    return n_copied, skipped[0]


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

    Handles all dependency forms including ``scope``+``branch`` syntax
    (no explicit ``git`` key) and arbitrary key ordering within
    ``[[require]]`` blocks.
    """
    lakefile = bundle_project / "lakefile.toml"
    if not lakefile.is_file():
        return

    import re
    import tomllib
    text = lakefile.read_text()
    try:
        data = tomllib.loads(text)
    except Exception:
        return

    requires = data.get("require", [])
    if not requires:
        return

    # Identify deps that need rewriting: any with git or scope keys
    # (scope implies a git dep resolved by Lake)
    names_to_rewrite = set()
    for req in requires:
        name = req.get("name", "")
        if name and ("git" in req or "scope" in req):
            names_to_rewrite.add(name)

    if not names_to_rewrite:
        return

    # Keys to remove from git/scope dep blocks
    git_keys = {"git", "rev", "scope", "branch"}

    # Find [[require]] block boundaries in the raw text.
    # A block starts at [[require]] and ends at the next table header or EOF.
    header_re = re.compile(r"^\s*\[\[?\w", re.MULTILINE)
    require_re = re.compile(r"^\s*\[\[\s*require\s*\]\]", re.MULTILINE)

    blocks = []
    for m in require_re.finditer(text):
        start = m.start()
        # Find next header after this one
        end_match = header_re.search(text, m.end())
        end = end_match.start() if end_match else len(text)
        blocks.append((start, end))

    # Process blocks in reverse order to preserve character positions
    for block_start, block_end in reversed(blocks):
        block_text = text[block_start:block_end]

        # Extract name from this block, skipping comment lines
        name_match = re.search(
            r'^(?!\s*#)\s*name\s*=\s*"([^"]*)"', block_text, re.MULTILINE
        )
        if not name_match or name_match.group(1) not in names_to_rewrite:
            continue

        name = name_match.group(1)

        # Remove git-related key lines, insert path at the first removal
        lines = block_text.split("\n")
        new_lines = []
        path_added = False
        for line in lines:
            stripped = line.strip()
            is_git_key = any(
                re.match(rf"{k}\s*=", stripped) for k in git_keys
            )
            if is_git_key:
                if not path_added:
                    indent = line[: len(line) - len(line.lstrip())]
                    new_lines.append(
                        f'{indent}path = ".lake/packages/{name}"'
                    )
                    path_added = True
            else:
                new_lines.append(line)

        text = text[:block_start] + "\n".join(new_lines) + text[block_end:]

    lakefile.write_text(text)


def _rewrite_lakefile_lean_deps(bundle_project: Path) -> None:
    """Rewrite lakefile.lean git deps to path deps.

    Handles all three ``require`` name forms:

    * bare:      ``require foo from git "url" @ "rev"``
    * quoted:    ``require "foo" from git "url" @ "rev"``
    * guillemet: ``require «foo-bar» from git "url" @ "rev"``

    Each is rewritten to ``require <name> from ".lake/packages/<name>"``.
    """
    lakefile = bundle_project / "lakefile.lean"
    if not lakefile.is_file():
        return

    import re
    text = lakefile.read_text()
    # Match: require <name> from git "url" [@ "rev"]
    # <name> can be: bare word, "quoted", or «guillemet»
    # Anchored to start-of-line (with optional leading whitespace) to avoid
    # rewriting commented-out lines or partial identifier matches.
    pattern = (
        r'(?m)^(\s*require\s+'
        r'(?:"([^"]+)"|«([^»]+)»|(\w+))'
        r'\s+)from\s+git\s+"[^"]*"(\s*@\s*"[^"]*")?'
    )

    def replace_dep(m):
        prefix = m.group(1)
        name = m.group(2) or m.group(3) or m.group(4)
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
    needed_stems: set[str] | None = None,
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
        extra_include: Additional file glob patterns to copy from the project.
        needed_stems: Module stems from the import closure. When given,
            only build artifacts for these modules are copied into the
            bundle's ``.lake/`` tree. Pass ``None`` to copy everything
            (fallback when the import closure cannot be computed).
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

    # Copy the .lake/ directory, pruning build artifacts to only the
    # modules in the transitive import closure when available.  This
    # typically reduces a full-mathlib bundle from ~5000 modules to the
    # ~50-100 actually imported by the project.
    print("Copying project .lake directory...")
    src_lake = project_dir / ".lake"
    dst_lake = bundle_project / ".lake"
    if src_lake.is_dir():
        n_copied, n_skipped = copy_lake_selective(
            src_lake, dst_lake, needed_stems,
        )
        print(f"  {n_copied} files copied, {n_skipped} skipped (not in import closure)")

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
    elif platform.startswith("darwin"):
        launcher_src = templates_dir / "start_lean.sh"
        launcher_dst = bundle_dir / "Start_Lean.command"
    else:
        launcher_src = templates_dir / "start_lean.sh"
        launcher_dst = bundle_dir / "Start_Lean.sh"

    shutil.copy2(launcher_src, launcher_dst)
    if not platform.startswith("windows"):
        launcher_dst.chmod(0o755)

    print(f"Bundle assembled at: {bundle_dir}")
