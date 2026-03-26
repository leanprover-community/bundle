#!/usr/bin/env python3
"""Create a self-contained Lean 4 bundle for offline use.

Usage:
    python bundle.py https://github.com/PatrickMassot/MDD154 --platform windows-x64

This will:
1. Clone the project
2. Download Lean, VSCodium, and the lean4 extension
3. Build the project and fetch cached oleans
4. Compute the transitive import closure
5. Assemble a bundle with only the needed oleans
6. Package it into a zip file
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from assemble import assemble_bundle
from download import (
    PLATFORM_MAP,
    download_lean4_extension,
    download_lean_toolchain,
    download_mingit,
    download_vscodium,
    parse_toolchain,
    trim_lean_toolchain,
)


def clone_project(repo_url: str, dest: Path) -> Path:
    print(f"Cloning {repo_url}...")
    subprocess.run(
        ["git", "clone", "--depth=1", repo_url, str(dest)],
        check=True,
    )
    return dest


def build_project(project_dir: Path) -> None:
    """Build the project: fetch cache and run lake build."""
    print("Fetching mathlib cache...")
    result = subprocess.run(
        ["lake", "exe", "cache", "get"],
        cwd=project_dir,
        capture_output=True,
        text=True,
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
    )


def create_zip(bundle_dir: Path, output_path: Path) -> None:
    print(f"Creating {output_path}...")
    with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(bundle_dir.rglob("*")):
            if path.is_file():
                arcname = path.relative_to(bundle_dir.parent)
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
        "--no-zip",
        action="store_true",
        help="Skip zip creation, just assemble the bundle directory",
    )

    args = parser.parse_args()

    if args.platform is None:
        import platform
        machine = platform.machine().lower()
        system = platform.system().lower()
        if system == "windows":
            args.platform = "windows-x64"
        elif system == "linux":
            args.platform = "linux-x64"
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
            project_dir = clone_project(args.repo_url, work_dir / "project")

        lean_version = parse_toolchain(project_dir / "lean-toolchain")
        print(f"Lean version: {lean_version}")

        print("\n--- Downloading components ---")
        downloads_dir = work_dir / "downloads"
        downloads_dir.mkdir(exist_ok=True)

        lean_dir = download_lean_toolchain(lean_version, args.platform, downloads_dir)
        vscodium_dir = download_vscodium(args.platform, downloads_dir, args.vscodium_version)
        extension_dirs = download_lean4_extension(downloads_dir, args.extension_version)
        mingit_dir = download_mingit(downloads_dir, args.platform)

        if not args.project_dir:
            print("\n--- Building project ---")
            build_project(project_dir)

        print("\n--- Trimming lean toolchain ---")
        trim_lean_toolchain(lean_dir, args.platform)

        print("\n--- Assembling bundle ---")
        bundle_dir = work_dir / f"{project_name}-bundle"
        assemble_bundle(
            project_dir=project_dir,
            lean_dir=lean_dir,
            vscodium_dir=vscodium_dir,
            extension_dirs=extension_dirs,
            mingit_dir=mingit_dir,
            templates_dir=templates_dir,
            bundle_dir=bundle_dir,
            platform=args.platform,
        )

        if not args.no_zip:
            print("\n--- Creating zip ---")
            output = args.output or Path(f"{project_name}-bundle-{args.platform}.zip")
            create_zip(bundle_dir, output)

    finally:
        if temp_dir and not args.work_dir:
            print(f"Cleaning up {temp_dir}")
            shutil.rmtree(temp_dir, ignore_errors=True)


if __name__ == "__main__":
    main()
