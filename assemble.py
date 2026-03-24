"""Assemble the bundle directory structure.

Takes the downloaded components and project files and arranges them
into the final bundle layout.
"""

import json
import shutil
from pathlib import Path

from import_closure import (
    build_search_paths,
    compute_closure,
    module_to_olean_paths,
    module_to_relpath,
)


def _copy_file(src: Path, dst: Path) -> None:
    """Copy a file, creating parent directories as needed."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


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
        # Determine which package this module belongs to
        for pkg_name, build_dir in build_dirs.items():
            # Try to find oleans in this build dir
            for olean_rel in module_to_olean_paths(mod):
                src = build_dir / olean_rel
                if src.is_file():
                    dst = bundle_packages_dir / pkg_name / ".lake" / "build" / "lib" / "lean" / olean_rel
                    _copy_file(src, dst)
                    oleans_copied += 1

            # Try to find source in this package
            if pkg_name in pkg_source_dirs:
                source_rel = module_to_relpath(mod)
                src = pkg_source_dirs[pkg_name] / source_rel
                if src.is_file():
                    dst = bundle_packages_dir / pkg_name / source_rel
                    _copy_file(src, dst)
                    sources_copied += 1

    return oleans_copied, sources_copied


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


def create_git_stubs(
    bundle_packages_dir: Path,
    manifest: dict,
) -> None:
    """Create minimal .git stubs so lake serve can read HEAD revisions.

    Args:
        bundle_packages_dir: The .lake/packages/ directory in the bundle.
        manifest: Parsed lake-manifest.json content.
    """
    for pkg in manifest.get("packages", []):
        pkg_name = pkg["name"]
        rev = pkg.get("rev")
        if not rev:
            continue
        pkg_dir = bundle_packages_dir / pkg_name
        if not pkg_dir.is_dir():
            continue
        git_dir = pkg_dir / ".git"
        git_dir.mkdir(exist_ok=True)
        (git_dir / "HEAD").write_text(rev + "\n")


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

    # Install extension
    ext_dest = data_dir / "extensions" / extension_dir.name
    if ext_dest.exists():
        shutil.rmtree(ext_dest)
    shutil.copytree(extension_dir, ext_dest)

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
    search_paths = build_search_paths(project_dir, toolchain_lib)
    needed = compute_closure(project_dir, search_paths)
    print(f"  {len(needed)} modules in transitive closure")

    print("Copying pruned dependency oleans and sources...")
    # Build maps of package -> build dir and package -> source dir
    packages_dir = project_dir / ".lake" / "packages"
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
        needed, build_dirs, bundle_packages, source_dirs
    )
    print(f"  {n_oleans} olean files copied")
    print(f"  {n_sources} source files copied")

    print("Creating git stubs...")
    manifest_path = project_dir / "lake-manifest.json"
    if manifest_path.is_file():
        manifest = json.loads(manifest_path.read_text())
        create_git_stubs(bundle_packages, manifest)

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
