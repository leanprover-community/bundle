"""Assemble the bundle directory structure.

Takes the downloaded components and project files and arranges them
into the final bundle layout.
"""

import json
import shutil
from pathlib import Path

from import_closure import compute_src_deps, find_module_build_artifacts, module_to_relpath


def _copy_file(src: Path, dst: Path) -> None:
    """Copy a file, creating parent directories as needed."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def classify_dep_source(
    src: Path,
    project_dir: Path,
    packages_dir: Path,
    toolchain_lib: Path,
) -> tuple[str, str | None] | None:
    """Map a dependency source path to its module name and owning package.

    Project sources are returned with `pkg_name=None` because they are copied
    separately; package and toolchain sources include the package key used by
    `copy_pruned_oleans`.
    """
    if src.suffix != ".lean":
        return None

    try:
        rel = src.relative_to(packages_dir)
    except ValueError:
        rel = None
    if rel and len(rel.parts) >= 2:
        pkg_name = rel.parts[0]
        mod = ".".join(Path(*rel.parts[1:]).with_suffix("").parts)
        return mod, pkg_name

    try:
        rel = src.relative_to(toolchain_lib)
    except ValueError:
        rel = None
    if rel and rel.parts:
        mod = ".".join(rel.with_suffix("").parts)
        return mod, "_toolchain"

    try:
        rel = src.relative_to(project_dir)
    except ValueError:
        rel = None
    if rel and ".lake" not in rel.parts:
        mod = ".".join(rel.with_suffix("").parts)
        return mod, None

    return None


def copy_project_files(project_dir: Path, bundle_project: Path) -> None:
    """Copy the project's own source files into the bundle.

    Copies .lean files, lakefile, lean-toolchain, lake-manifest.json,
    and .vscode/ settings. Skips .lake/ and .git/.
    """
    skip = {".lake", ".git", ".github", "lake-packages"}

    for item in sorted(project_dir.iterdir()):
        if item.name in skip:
            continue
        dst = bundle_project / item.name
        if item.is_file():
            _copy_file(item, dst)
        elif item.is_dir():
            shutil.copytree(item, dst, dirs_exist_ok=True)


def copy_pruned_oleans(
    needed_modules: set[str],
    module_to_pkg: dict[str, str],
    build_dirs: dict[str, Path],
    bundle_packages_dir: Path,
    pkg_source_dirs: dict[str, Path],
) -> tuple[int, int]:
    """Copy only the transitively needed oleans and sources into the bundle.

    Args:
        needed_modules: Set of module names from the transitive closure.
        build_dirs: Map from package name -> build lib directory
            (e.g. {"mathlib": Path(".lake/packages/mathlib/.lake/build/lib/lean")}).
        bundle_packages_dir: Where to put packages in the bundle.
        pkg_source_dirs: Map from package name -> source directory for .lean files.

    Returns:
        Tuple of (oleans_copied, sources_copied).
    """
    oleans_copied = 0
    sources_copied = 0

    for mod in sorted(needed_modules):
        pkg_name = module_to_pkg.get(mod)
        if not pkg_name:
            continue
        build_dir = build_dirs.get(pkg_name)
        if build_dir:
            for rel_path, abs_path in find_module_build_artifacts(mod, build_dir):
                dst = bundle_packages_dir / pkg_name / ".lake" / "build" / "lib" / "lean" / rel_path
                _copy_file(abs_path, dst)
                oleans_copied += 1

        source_dir = pkg_source_dirs.get(pkg_name)
        if source_dir:
            source_rel = module_to_relpath(mod)
            src = source_dir / source_rel
            if src.is_file():
                dst = bundle_packages_dir / pkg_name / source_rel
                _copy_file(src, dst)
                sources_copied += 1

    return oleans_copied, sources_copied


def copy_package_configs(
    packages_dir: Path,
    bundle_packages_dir: Path,
) -> None:
    """Copy package configuration files (lakefile, lean-toolchain, etc.).

    Lake needs these to load the workspace even with --no-build.
    """
    config_files = [
        "lakefile.toml", "lakefile.lean", "lean-toolchain",
        "lake-manifest.json", "lakefile",
    ]
    if not packages_dir.is_dir():
        return
    for pkg in sorted(packages_dir.iterdir()):
        if not pkg.is_dir():
            continue
        dst_pkg = bundle_packages_dir / pkg.name
        for cf in config_files:
            src = pkg / cf
            if src.is_file():
                _copy_file(src, dst_pkg / cf)


def copy_project_oleans(project_dir: Path, bundle_project: Path) -> int:
    """Copy the project's own oleans into the bundle.

    Returns the number of oleans copied.
    """
    build_dir = project_dir / ".lake" / "build" / "lib" / "lean"
    if not build_dir.is_dir():
        return 0

    count = 0
    bundle_build = bundle_project / ".lake" / "build" / "lib" / "lean"
    for f in build_dir.rglob("*"):
        if f.is_file() and f.suffix in (".olean", ".ilean"):
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

    Lake's validateManifest only warns on source-kind mismatch, so this works.
    """
    manifest_path = bundle_project / "lake-manifest.json"
    if not manifest_path.is_file():
        return

    manifest = json.loads(manifest_path.read_text())
    packages_dir = bundle_project / ".lake" / "packages"

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


def setup_vscodium_portable(
    vscodium_dir: Path,
    extension_dir: Path,
    settings_template: Path,
) -> None:
    """Set up VSCodium in portable mode with the lean4 extension pre-installed.

    Args:
        vscodium_dir: Path to extracted VSCodium.
        extension_dir: Path to extracted lean4 extension.
        settings_template: Path to settings.json template.
    """
    # Create portable data directory
    data_dir = vscodium_dir / "data"
    data_dir.mkdir(exist_ok=True)

    # VSIX archives often contain their actual extension payload under
    # `extension/`; VSCodium expects package.json at the extension root.
    extension_root = extension_dir / "extension"
    if not extension_root.is_dir():
        extension_root = extension_dir

    # Install extension
    extensions_dir = data_dir / "extensions"
    ext_dest = extensions_dir / extension_dir.name
    if ext_dest.exists():
        shutil.rmtree(ext_dest)
    shutil.copytree(extension_root, ext_dest)

    # Read extension metadata for the registry
    ext_package = ext_dest / "package.json"
    ext_id = extension_dir.name  # e.g. "leanprover.lean4-0.0.225"
    ext_version = "0.0.0"
    ext_publisher = "leanprover"
    ext_name = "lean4"
    if ext_package.is_file():
        pkg = json.loads(ext_package.read_text())
        ext_version = pkg.get("version", ext_version)
        ext_publisher = pkg.get("publisher", ext_publisher)
        ext_name = pkg.get("name", ext_name)

    # Write extensions.json registry so --list-extensions works
    registry = [
        {
            "identifier": {"id": f"{ext_publisher}.{ext_name}"},
            "version": ext_version,
            "relativeLocation": extension_dir.name,
            "metadata": {},
        }
    ]
    (extensions_dir / "extensions.json").write_text(json.dumps(registry, indent=2) + "\n")

    # Create user settings
    user_dir = data_dir / "user-data" / "User"
    user_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy2(settings_template, user_dir / "settings.json")


def assemble_bundle(
    project_dir: Path,
    lean_dir: Path,
    vscodium_dir: Path,
    extension_dir: Path,
    templates_dir: Path,
    bundle_dir: Path,
    platform: str,
) -> None:
    """Assemble the complete bundle directory.

    Args:
        project_dir: The built project directory (with .lake/packages/ and oleans).
        lean_dir: Extracted and trimmed lean toolchain directory.
        vscodium_dir: Extracted VSCodium directory.
        extension_dir: Extracted lean4 extension directory.
        templates_dir: Directory containing launcher and settings templates.
        bundle_dir: Output bundle directory to create.
        platform: Target platform key.
    """
    bundle_dir.mkdir(parents=True, exist_ok=True)

    print("Copying lean toolchain...")
    shutil.copytree(lean_dir, bundle_dir / "lean", dirs_exist_ok=True)

    print("Setting up VSCodium...")
    shutil.copytree(vscodium_dir, bundle_dir / "vscodium", dirs_exist_ok=True)
    setup_vscodium_portable(
        bundle_dir / "vscodium",
        extension_dir,
        templates_dir / "settings.json",
    )

    print("Copying project files...")
    bundle_project = bundle_dir / "project"
    copy_project_files(project_dir, bundle_project)

    print("Copying project oleans...")
    n_proj = copy_project_oleans(project_dir, bundle_project)
    print(f"  {n_proj} project olean files copied")

    print("Computing import closure...")
    toolchain_lib = bundle_dir / "lean" / "lib" / "lean"
    dep_sources = compute_src_deps(project_dir)
    print(f"  {len(dep_sources)} source files in transitive deps")

    needed: set[str] = set()
    module_to_pkg: dict[str, str] = {}
    packages_dir = project_dir / ".lake" / "packages"

    for src in dep_sources:
        classified = classify_dep_source(src, project_dir, packages_dir, toolchain_lib)
        if classified is None:
            continue
        mod, pkg_name = classified
        needed.add(mod)
        if pkg_name:
            module_to_pkg[mod] = pkg_name

    print(f"  {len(needed)} modules in transitive closure")

    print("Copying pruned dependency oleans and sources...")
    # Build maps of package -> build dir and package -> source dir
    build_dirs: dict[str, Path] = {}
    source_dirs: dict[str, Path] = {}

    if packages_dir.is_dir():
        for pkg in sorted(packages_dir.iterdir()):
            if pkg.is_dir():
                build_lib = pkg / ".lake" / "build" / "lib" / "lean"
                if build_lib.is_dir():
                    build_dirs[pkg.name] = build_lib
                source_dirs[pkg.name] = pkg

    # Also include toolchain oleans for Init, Lean, Std, Lake
    build_dirs["_toolchain"] = lean_dir / "lib" / "lean"

    bundle_packages = bundle_project / ".lake" / "packages"
    n_oleans, n_sources = copy_pruned_oleans(
        needed, module_to_pkg, build_dirs, bundle_packages, source_dirs
    )
    print(f"  {n_oleans} olean files copied")
    print(f"  {n_sources} source files copied")

    print("Copying package config files...")
    copy_package_configs(packages_dir, bundle_packages)

    print("Rewriting manifest to path deps...")
    rewrite_manifest_to_path_deps(bundle_project)

    print("Installing launcher...")
    if platform.startswith("windows"):
        launcher_src = templates_dir / "start_lean.cmd"
        launcher_dst = bundle_dir / "Start Lean.cmd"
    else:
        launcher_src = templates_dir / "start_lean.sh"
        launcher_dst = bundle_dir / "Start Lean.sh"

    shutil.copy2(launcher_src, launcher_dst)
    if not platform.startswith("windows"):
        launcher_dst.chmod(0o755)

    print(f"Bundle assembled at: {bundle_dir}")
