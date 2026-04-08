#!/usr/bin/env python3
"""Create a self-contained Lean 4 bundle for offline use.

Usage:
    python bundle.py https://github.com/PatrickMassot/MDD154 --platform windows

This will:
1. Clone the project and build it (fetching mathlib cache)
2. Download Lean, VSCodium, and the lean4 extension
3. Compute the transitive import closure (via ``lean --src-deps``)
4. Assemble a bundle containing only the needed oleans
5. Package it into a zip file
"""

import argparse
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from assemble import assemble_bundle
from import_closure import compute_src_deps, src_paths_to_module_stems
from download import (
    PLATFORM_MAP,
    build_git_shim,
    download_lean4_extension,
    download_lean_toolchain,
    download_vscodium,
    parse_toolchain,
    trim_lean_toolchain,
)


def clone_project(repo_url: str, dest: Path, ref: str | None = None) -> Path:
    print(f"Cloning {repo_url}...")
    cmd = ["git", "clone", "--depth=1"]
    if ref:
        cmd += ["--branch", ref]
    cmd += [repo_url, str(dest)]
    subprocess.run(cmd, check=True, timeout=300)
    return dest


def build_project(project_dir: Path) -> None:
    """Build the project: fetch cache and run lake build."""
    print("Fetching mathlib cache...")
    result = subprocess.run(
        ["lake", "exe", "cache", "get"],
        cwd=project_dir,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        print(f"  Warning: cache get returned {result.returncode}")
        if result.stderr:
            print(f"  {result.stderr[:500]}")

    print("Building project...")
    subprocess.run(
        ["lake", "build"],
        cwd=project_dir,
        check=True,
        timeout=1800,
    )


def create_zip(bundle_dir: Path, output_path: Path) -> None:
    print(f"Creating {output_path}...")

    # Compute the latest .lean source mtime so we can force all olean/ilean
    # entries to be strictly newer in the zip.  DOS timestamps have 2-second
    # granularity, so a small offset isn't reliable — we use 2 full minutes.
    import datetime as _dt
    lean_max_mtime = max(
        (f.stat().st_mtime for f in bundle_dir.rglob("*.lean") if f.is_file()),
        default=0,
    )
    # Convert to a datetime 2 minutes after the latest source, rounded up
    # to the next even second (DOS granularity).
    olean_ts = _dt.datetime.fromtimestamp(lean_max_mtime + 120)
    olean_date_time = (
        olean_ts.year, olean_ts.month, olean_ts.day,
        olean_ts.hour, olean_ts.minute, olean_ts.second & ~1,  # even second
    )

    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(bundle_dir.rglob("*")):
            arcname = str(path.relative_to(bundle_dir.parent))
            if path.is_symlink():
                # Store symlinks with the Unix symlink type flag
                info = zipfile.ZipInfo(arcname)
                info.external_attr = (0o120755 << 16)  # S_IFLNK | 0755
                info.compress_type = zipfile.ZIP_STORED
                zf.writestr(info, str(os.readlink(path)))
            elif path.is_file():
                if path.suffix in (".olean", ".ilean"):
                    # Force olean/ilean to a timestamp well after the latest
                    # .lean source so they survive DOS timestamp rounding.
                    info = zipfile.ZipInfo.from_file(path, arcname)
                    info.date_time = olean_date_time
                    info.compress_type = zipfile.ZIP_DEFLATED
                    zf.writestr(info, path.read_bytes())
                else:
                    # zf.write() streams the file and preserves Unix permissions
                    zf.write(path, arcname)

    size_mb = output_path.stat().st_size / (1024 * 1024)
    with open(output_path, "rb") as f:
        digest = hashlib.file_digest(f, "sha256").hexdigest()
    print(f"Bundle created: {output_path} ({size_mb:.1f} MB)")
    print(f"SHA-256: {digest}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create a self-contained Lean 4 bundle for offline use.",
    )
    parser.add_argument(
        "repo_url",
        help="GitHub repository URL (e.g. https://github.com/PatrickMassot/MDD154)",
    )
    parser.add_argument(
        "--platform",
        choices=list(PLATFORM_MAP.keys()),
        default=None,
        help="Target platform (default: auto-detect from current OS)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output zip file path (default: <project>-bundle-<platform>.zip)",
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=None,
        help="Working directory for downloads and builds (default: temp dir)",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Use an already-cloned and built project directory instead of cloning",
    )
    parser.add_argument(
        "--ref",
        default=None,
        help="Git ref to checkout (branch or tag)",
    )
    parser.add_argument(
        "--vscodium-version",
        default=None,
        help="VSCodium version to use (default: latest)",
    )
    parser.add_argument(
        "--extension-version",
        default=None,
        help="lean4 VS Code extension version (default: latest)",
    )
    parser.add_argument(
        "--include",
        nargs="*",
        default=[],
        help="Additional file patterns to include from project (e.g. '*.json' 'data/')",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Skip zip creation, just assemble the bundle directory",
    )

    args = parser.parse_args()

    if args.no_zip and not args.work_dir:
        parser.error("--no-zip requires --work-dir (otherwise the bundle is assembled in a temp dir that gets cleaned up)")

    if args.platform is None:
        import platform
        machine = platform.machine().lower()
        system = platform.system().lower()
        if system == "windows":
            args.platform = "windows"
        elif system == "linux":
            args.platform = "linux-arm64" if machine in ("arm64", "aarch64") else "linux-x64"
        elif system == "darwin":
            args.platform = "darwin-arm64" if machine in ("arm64", "aarch64") else "darwin-x64"
        else:
            parser.error(f"Cannot auto-detect platform for {system}/{machine}. Use --platform.")

    project_name = args.repo_url.rstrip("/").split("/")[-1]

    temp_dir = None
    if args.work_dir:
        work_dir = args.work_dir
        work_dir.mkdir(parents=True, exist_ok=True)
    else:
        temp_dir = tempfile.mkdtemp(prefix="lean-bundle-")
        work_dir = Path(temp_dir)

    try:
        templates_dir = Path(__file__).parent / "templates"

        if args.project_dir:
            project_dir = args.project_dir
            print(f"Using existing project at {project_dir}")
        else:
            project_dir = clone_project(args.repo_url, work_dir / "project", ref=args.ref)

        lean_version = parse_toolchain(project_dir / "lean-toolchain")
        print(f"Lean version: {lean_version}")

        print("\n--- Downloading components ---")
        downloads_dir = work_dir / "downloads"
        downloads_dir.mkdir(exist_ok=True)

        lean_dir = download_lean_toolchain(lean_version, args.platform, downloads_dir)
        vscodium_dir, vscodium_version = download_vscodium(args.platform, downloads_dir, args.vscodium_version)
        extension_dirs, extension_version = download_lean4_extension(downloads_dir, args.extension_version)
        git_shim_exe = build_git_shim(downloads_dir, args.platform)

        if not args.project_dir:
            print("\n--- Building project ---")
            build_project(project_dir)

        print("\n--- Trimming lean toolchain ---")
        trim_lean_toolchain(lean_dir, args.platform)

        print("\n--- Computing import closure ---")
        needed_stems: set[str] | None = None
        try:
            needed_srcs = compute_src_deps(project_dir)
            needed_stems = src_paths_to_module_stems(needed_srcs, project_dir)
            print(f"  {len(needed_stems)} modules in transitive closure")
        except Exception as e:
            print(f"  Warning: could not compute import closure: {e}")
            print("  Falling back to copying all build artifacts")

        # Sanity-check: verify that at least one computed stem corresponds
        # to an actual .olean file.  If a package uses Lake's srcDir option
        # the source→stem mapping is wrong and we must fall back to a full
        # copy rather than silently dropping needed modules.
        if needed_stems:
            verified = False
            for bldir in (project_dir / ".lake").rglob("build/lib/lean"):
                if not bldir.is_dir():
                    continue
                for stem in needed_stems:
                    if (bldir / stem).with_suffix(".olean").is_file():
                        verified = True
                        break
                if verified:
                    break
            if not verified:
                print("  Warning: computed stems don't match any build artifacts")
                print("  Falling back to copying all build artifacts")
                needed_stems = None

        print("\n--- Assembling bundle ---")
        bundle_dir = work_dir / f"{project_name}-bundle"
        assemble_bundle(
            project_dir=project_dir,
            lean_dir=lean_dir,
            vscodium_dir=vscodium_dir,
            extension_dirs=extension_dirs,
            git_shim_exe=git_shim_exe,
            templates_dir=templates_dir,
            bundle_dir=bundle_dir,
            platform=args.platform,
            extra_include=args.include,
            needed_stems=needed_stems,
        )

        # Write bundle manifest for reproducibility
        manifest = {
            "lean_version": lean_version,
            "vscodium_version": vscodium_version,
            "extension_version": extension_version,
            "platform": args.platform,
            "repo_url": args.repo_url,
            "ref": args.ref,
            "include": args.include or None,
            "built_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        (bundle_dir / "bundle-manifest.json").write_text(
            json.dumps(manifest, indent=2) + "\n"
        )

        if not args.no_zip:
            print("\n--- Creating zip ---")
            output = args.output or Path(f"{project_name}-bundle-{args.platform}.zip")
            create_zip(bundle_dir, output)

    finally:
        if temp_dir and not args.work_dir and not args.no_zip:
            print(f"Cleaning up {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
