#!/usr/bin/env python3
"""Create a self-contained Lean 4 bundle for offline use.

Usage:
    python bundle.py https://github.com/PatrickMassot/MDD154 --platform windows

This will:
1. Clone the project and build it (fetching mathlib cache)
2. Download Lean, VSCodium, and the selected editor extension
3. Compute the transitive import closure (batched with ``lean --deps-json``)
4. Assemble a bundle containing only the needed oleans
5. Package it into a zip file
"""

import argparse
import datetime
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

from assemble import _rmtree, _windows_extended_path, assemble_bundle
from import_closure import compute_src_deps, src_paths_to_module_stems
from download import (
    PLATFORM_MAP,
    build_git_shim,
    download_lean4_extension,
    download_lean_toolchain,
    download_vscodium,
    download_waterproof_extension,
    install_local_waterproof_vsix,
    parse_toolchain,
    trim_lean_toolchain,
)


def clone_project(repo_url: str, dest: Path, ref: str | None = None) -> Path:
    if ref and ref.startswith("-"):
        raise ValueError("Git ref must not start with '-'")
    if dest.is_symlink() or (dest.exists() and not dest.is_dir()):
        raise ValueError(
            f"Project clone destination exists and is not a directory: {dest}"
        )
    if dest.is_dir():
        print(f"Removing previous project clone at {dest}...")
        _rmtree(dest)

    print(f"Cloning {repo_url}...")
    cmd = ["git", "clone", "--depth=1"]
    if ref:
        cmd.append("--no-checkout")
    cmd += [repo_url, str(dest)]
    subprocess.run(cmd, check=True, timeout=300)
    if ref:
        subprocess.run(
            ["git", "-C", str(dest), "fetch", "--depth=1", "origin", ref],
            check=True,
            timeout=300,
        )
        subprocess.run(
            ["git", "-C", str(dest), "checkout", "--detach", "FETCH_HEAD"],
            check=True,
            timeout=300,
        )
    return dest


_WORK_DIR_MARKER = ".lean-bundle-work-dir"


def _prepare_work_dir(
    requested: Path,
    *,
    clean: bool = False,
    protected_paths: tuple[Path, ...] = (),
) -> Path:
    """Create a work directory, or clean it when its ownership marker is valid."""
    work_dir = requested.resolve()
    if work_dir.exists() and not work_dir.is_dir():
        raise ValueError(f"Work directory exists and is not a directory: {work_dir}")

    marker = work_dir / _WORK_DIR_MARKER
    marker_content = f"lean-bundle work directory\n{work_dir}\n"

    if clean:
        guarded_paths = {
            Path.cwd().resolve(),
            Path(__file__).resolve().parent,
            Path.home().resolve(),
            Path(tempfile.gettempdir()).resolve(),
        }
        if work_dir in guarded_paths or any(
            work_dir in path.parents for path in guarded_paths
        ):
            raise ValueError(f"Refusing to remove unsafe work directory: {work_dir}")

        inputs = {path.resolve() for path in protected_paths}
        if work_dir in inputs or any(
            work_dir in path.parents or path in work_dir.parents for path in inputs
        ):
            raise ValueError(f"Work directory overlaps an input path: {work_dir}")

        if work_dir.exists():
            try:
                owned = (
                    not marker.is_symlink()
                    and marker.read_text(encoding="utf-8") == marker_content
                )
            except (OSError, UnicodeError):
                owned = False
            if not owned:
                raise ValueError(
                    f"Refusing to clean unowned work directory {work_dir}; "
                    f"{_WORK_DIR_MARKER} is missing or invalid"
                )
            print(f"Removing previous work directory at {work_dir}...")
            _rmtree(work_dir)

    if not work_dir.exists():
        work_dir.mkdir(parents=True)
        marker.write_text(marker_content, encoding="utf-8")
    return work_dir


def build_project(
    project_dir: Path,
    lake_executable: Path,
    bundle_platform: str,
    allow_unsolved: bool = False,
) -> None:
    """Materialize, cache, and build a project with the downloaded Lake.

    The source checkout supplied through ``--project-dir`` is deliberately
    allowed to be completely unbuilt.  Using the downloaded, version-matched
    Lake also means the build host does not need elan or Lean preinstalled.
    """
    build_env = os.environ.copy()
    lean_bin = lake_executable.parent
    build_env["PATH"] = str(lean_bin) + os.pathsep + build_env.get("PATH", "")
    build_env["ELAN_HOME"] = str(lean_bin.parent)

    print("Fetching mathlib cache...")
    result = subprocess.run(
        [str(lake_executable), "exe", "cache", "get"],
        cwd=project_dir,
        env=build_env,
        capture_output=True,
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        print(f"  Warning: cache get returned {result.returncode}")
        if result.stderr:
            print(f"  {result.stderr[:500]}")

    print("Building project...")
    build_cmd = [str(lake_executable), "build"]
    build_result = subprocess.run(
        build_cmd,
        cwd=project_dir,
        env=build_env,
        timeout=1800,
    )
    if build_result.returncode == 0:
        return

    # On Windows, companion artifacts such as .olean.private can
    # occasionally fail to read under heavy parallel load.  Lake preserves
    # every target that did build successfully, so retry the remaining work
    # with a single runtime worker.  A parallel retry can merely move the
    # transient failure to a later target instead of resolving it.
    if bundle_platform == "windows":
        print("  Initial build failed; retrying serially on Windows...")
        retry_env = build_env.copy()
        retry_env["LEAN_NUM_THREADS"] = "1"
        build_result = subprocess.run(
            build_cmd,
            cwd=project_dir,
            env=retry_env,
            timeout=1800,
        )
        if build_result.returncode == 0:
            return

    if allow_unsolved:
        print(
            "  Warning: project build failed, continuing because "
            "--allow-unsolved was specified"
        )
        return

    build_result.check_returncode()


def _detect_host_platform() -> str:
    """Return the bundle platform key for a supported host CPU and OS."""
    machine = platform.machine().lower()
    system = platform.system().lower()
    x64_machines = {"amd64", "x86_64"}
    arm64_machines = {"aarch64", "arm64"}

    if system == "windows" and machine in x64_machines:
        return "windows"
    if system == "linux" and machine in x64_machines:
        return "linux-x64"
    if system == "linux" and machine in arm64_machines:
        return "linux-arm64"
    if system == "darwin" and machine in x64_machines:
        return "darwin-x64"
    if system == "darwin" and machine in arm64_machines:
        return "darwin-arm64"
    raise RuntimeError(f"Cannot detect a supported platform for {system}/{machine}")


def _download_editor_extensions(
    downloads_dir: Path,
    *,
    waterproof: bool,
    lean4_version: str | None = None,
    waterproof_version: str | None = None,
    waterproof_vsix: Path | None = None,
) -> tuple[list[Path], str, str]:
    """Download exactly one editor frontend and its declared dependencies.

    Both extensions activate for ``.lean`` files and start a Lean language
    server. A Waterproof bundle is already an isolated editor environment,
    so it contains Waterproof instead of Lean 4 rather than needing another
    VS Code profile.
    """
    if waterproof:
        print("Downloading Waterproof extension (Lean genre only)...")
        if waterproof_vsix is not None:
            extension_dirs, resolved_waterproof_version = install_local_waterproof_vsix(
                waterproof_vsix, downloads_dir
            )
        else:
            extension_dirs, resolved_waterproof_version = download_waterproof_extension(
                downloads_dir, waterproof_version
            )
        return (
            extension_dirs,
            "waterproof-tue.waterproof",
            resolved_waterproof_version,
        )

    extension_dirs, resolved_lean4_version = download_lean4_extension(
        downloads_dir, lean4_version
    )
    return extension_dirs, "leanprover.lean4", resolved_lean4_version


class _ImportClosureProgress:
    """Render per-wave progress without flooding redirected build logs."""

    def __init__(self) -> None:
        self._interactive = sys.stdout.isatty()
        self._last_wave = 0
        self._last_bucket = -1
        self._last_width = 0

    def __call__(
        self,
        wave: int,
        completed: int,
        wave_total: int,
        checked_total: int,
        dependencies_found: int,
    ) -> None:
        percent = completed * 100 // wave_total
        bucket = percent // 10

        if not self._interactive:
            if wave == self._last_wave and completed not in (0, wave_total):
                if bucket == self._last_bucket:
                    return
        self._last_wave = wave
        self._last_bucket = bucket

        message = (
            f"  Wave {wave}: {completed}/{wave_total} files ({percent}%)"
            f" | {checked_total} checked, {dependencies_found} dependencies found"
        )
        if self._interactive:
            padding = " " * max(0, self._last_width - len(message))
            print(
                f"\r{message}{padding}",
                end="\n" if completed == wave_total else "",
                flush=True,
            )
            self._last_width = 0 if completed == wave_total else len(message)
        else:
            print(message, flush=True)


def create_zip(bundle_dir: Path, output_path: Path) -> None:
    print(f"Creating {output_path}...")
    archive_root = _windows_extended_path(bundle_dir)
    archive_output = _windows_extended_path(output_path)

    # Compute the latest .lean source mtime so we can force all olean/ilean
    # entries to be strictly newer in the zip.  DOS timestamps have 2-second
    # granularity, so a small offset isn't reliable — we use 2 full minutes.
    import datetime as _dt
    lean_max_mtime = max(
        (f.stat().st_mtime for f in archive_root.rglob("*.lean") if f.is_file()),
        default=0,
    )
    # Convert to a datetime 2 minutes after the latest source, rounded up
    # to the next even second (DOS granularity).
    olean_ts = _dt.datetime.fromtimestamp(lean_max_mtime + 120)
    olean_date_time = (
        olean_ts.year, olean_ts.month, olean_ts.day,
        olean_ts.hour, olean_ts.minute, olean_ts.second & ~1,  # even second
    )

    with zipfile.ZipFile(archive_output, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(archive_root.rglob("*")):
            arcname = str(path.relative_to(archive_root.parent))
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

    size_mb = archive_output.stat().st_size / (1024 * 1024)
    with open(archive_output, "rb") as f:
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
        help="Native bundle platform; must match the current host "
             "(default: auto-detect)",
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
        help="Working directory for downloads and builds (default: a fresh "
             "temporary directory)",
    )
    parser.add_argument(
        "--clean-work-dir",
        action="store_true",
        help="Remove the entire --work-dir before building; requires a valid "
             "lean-bundle ownership marker when the directory already exists",
    )
    parser.add_argument(
        "--project-dir",
        type=Path,
        default=None,
        help="Use an already-cloned project directory instead of cloning; "
             "dependencies are fetched and the project is built automatically",
    )
    parser.add_argument(
        "--allow-unsolved",
        action="store_true",
        help="Continue if the project build fails because exercises contain "
             "unsolved goals.",
    )
    parser.add_argument(
        "--ref",
        default=None,
        help="Git ref to checkout (commit, branch, or tag)",
    )
    parser.add_argument(
        "--vscodium-version",
        default=None,
        help="VSCodium version to use (default: latest)",
    )
    parser.add_argument(
        "--extension-version",
        default=None,
        help="lean4 VS Code extension version (default: latest; unavailable "
             "in Waterproof mode)",
    )
    parser.add_argument(
        "--waterproof",
        action="store_true",
        help="Bundle the Waterproof VS Code extension instead of the "
             "conflicting Lean 4 extension, for projects "
             "using the Waterproof Lean genre (impermeable/waterproof-genre). "
             "Only the Lean path is wired up: the bundle's workspace "
             "settings restrict Waterproof to its Lean language server "
             "(waterproof.skipLaunchChecks=lean4) and no Rocq/coq-lsp "
             "components are downloaded or configured. Fetched from Open VSX.",
    )
    parser.add_argument(
        "--waterproof-version",
        default=None,
        help="Waterproof extension version to fetch from Open VSX "
             "(implies --waterproof; default: latest). Mutually exclusive "
             "with --waterproof-vsix.",
    )
    parser.add_argument(
        "--waterproof-vsix",
        type=Path,
        default=None,
        help="Path to an unpublished or locally-built Waterproof .vsix "
             "(implies --waterproof).",
    )
    parser.add_argument(
        "--include",
        nargs="*",
        default=[],
        help="Additional file patterns to include from project (e.g. '*.json' 'data/')",
    )
    parser.add_argument(
        "--open-file",
        default=None,
        help="Lean file to open on the bundle's first launch "
             "(e.g. 'LibDM3.lean'). If not specified, the workspace opens "
             "without an editor tab. Not supported for Waterproof bundles "
             "on Windows.",
    )
    parser.add_argument(
        "--no-zip",
        action="store_true",
        help="Skip zip creation, just assemble the bundle directory",
    )

    args = parser.parse_args()
    if args.clean_work_dir and not args.work_dir:
        parser.error("--clean-work-dir requires --work-dir")

    if args.waterproof_version and args.waterproof_vsix:
        parser.error("--waterproof-version and --waterproof-vsix are mutually exclusive")

    include_waterproof = (
        args.waterproof
        or args.waterproof_version is not None
        or args.waterproof_vsix is not None
    )

    if include_waterproof and args.extension_version is not None:
        parser.error(
            "--extension-version cannot be used in Waterproof mode: "
            "the conflicting Lean 4 extension is omitted"
        )

    if args.no_zip and not args.work_dir:
        parser.error("--no-zip requires --work-dir (otherwise the bundle is assembled in a temp dir that gets cleaned up)")

    try:
        host_platform = _detect_host_platform()
    except RuntimeError as e:
        parser.error(str(e))
    if args.platform is None:
        args.platform = host_platform
    elif args.platform != host_platform:
        parser.error(
            "Cross-platform bundles are not supported: "
            f"host is {host_platform}, requested {args.platform}. "
            f"Build --platform {args.platform} on that platform instead."
        )

    if (
        args.platform == "windows"
        and include_waterproof
        and args.open_file is not None
    ):
        parser.error(
            "--open-file is not supported for Waterproof bundles on Windows: "
            "a VS Code cold-start bug ignores workbench.editorAssociations "
            "for file arguments"
        )

    project_name = args.repo_url.rstrip("/").split("/")[-1]

    temp_dir = None
    if args.work_dir:
        protected_inputs = tuple(
            path.resolve()
            for path in (args.project_dir, args.waterproof_vsix)
            if path is not None
        )
        try:
            work_dir = _prepare_work_dir(
                args.work_dir,
                clean=args.clean_work_dir,
                protected_paths=protected_inputs,
            )
        except ValueError as e:
            parser.error(str(e))
    else:
        temp_dir = tempfile.mkdtemp(prefix="lean-bundle-")
        work_dir = Path(temp_dir)

    try:
        templates_dir = Path(__file__).parent / "templates"

        if args.project_dir:
            project_dir = args.project_dir.resolve()
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
        extension_dirs, editor_extension, extension_version = (
            _download_editor_extensions(
                downloads_dir,
                waterproof=include_waterproof,
                lean4_version=args.extension_version,
                waterproof_version=args.waterproof_version,
                waterproof_vsix=args.waterproof_vsix,
            )
        )

        git_shim_exe = build_git_shim(
            downloads_dir,
            args.platform,
            lean_dir=lean_dir,
        )

        lake_executable = (
            lean_dir
            / "bin"
            / ("lake.exe" if args.platform == "windows" else "lake")
        )

        print("\n--- Building project ---")
        build_project(
            project_dir,
            lake_executable,
            args.platform,
            allow_unsolved=args.allow_unsolved,
        )

        print("\n--- Computing import closure ---")
        needed_stems: set[str] | None = None
        try:
            closure_progress = _ImportClosureProgress()
            needed_srcs = compute_src_deps(
                project_dir,
                progress=closure_progress,
                lake_executable=lake_executable,
            )
            needed_stems = src_paths_to_module_stems(needed_srcs, project_dir)
            print(f"  {len(needed_stems)} modules in transitive closure")
        except Exception as e:
            print(f"  Warning: could not compute import closure: {e}")
            print("  Falling back to copying all build artifacts")

        # Sanity-check: verify that at least one computed stem corresponds
        # to an actual .olean file. Source paths are resolved against package
        # build trees (including srcDir layouts), but a completely unbuilt or
        # unusual project should still fall back to copying all artifacts
        # rather than silently dropping every dependency module.
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
            open_file=args.open_file,
            waterproof_included=include_waterproof,
            allow_unsolved=args.allow_unsolved,
        )

        print("\n--- Trimming bundled lean toolchain ---")
        trim_lean_toolchain(bundle_dir / "lean", args.platform)

        # Write bundle manifest for reproducibility
        manifest = {
            "lean_version": lean_version,
            "vscodium_version": vscodium_version,
            "extension_version": extension_version,
            "editor_extension": editor_extension,
            "platform": args.platform,
            "repo_url": args.repo_url,
            "ref": args.ref,
            "include": args.include or None,
            "allow_unsolved": args.allow_unsolved,
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
            try:
                _rmtree(Path(temp_dir))
            except OSError:
                pass


if __name__ == "__main__":
    main()
