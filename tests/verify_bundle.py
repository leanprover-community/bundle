#!/usr/bin/env python3
"""Verify a bundle's structural integrity.

Checks that all expected files, directories, DLLs, extensions, settings,
and git stubs are present and correct.

Usage:
    python tests/verify_bundle.py <bundle_root> [--platform windows-x64]
"""

import json
import sys
from pathlib import Path


def verify(bundle_root: Path, platform: str = "windows-x64") -> list[str]:
    """Verify bundle structure. Returns list of error strings (empty = OK)."""
    errors: list[str] = []
    is_windows = platform.startswith("windows")

    lean_exe = "lean.exe" if is_windows else "lean"
    lake_exe = "lake.exe" if is_windows else "lake"
    vscodium_exe = "VSCodium.exe" if is_windows else "codium"
    launcher = "Start Lean.cmd" if is_windows else "Start Lean.sh"

    # --- Required files and directories ---
    required_files = [
        f"lean/bin/{lean_exe}",
        f"lean/bin/{lake_exe}",
        f"project/lean-toolchain",
        f"project/lake-manifest.json",
        launcher,
    ]
    required_dirs = [
        "lean/lib/lean/Init",
        "lean/lib/lean/Lean",
        "project/.lake/packages",
    ]

    for r in required_files:
        if not (bundle_root / r).is_file():
            errors.append(f"Missing file: {r}")
    for r in required_dirs:
        if not (bundle_root / r).is_dir():
            errors.append(f"Missing directory: {r}")

    # --- VSCodium ---
    vscodium_path = bundle_root / "vscodium" / vscodium_exe
    if not vscodium_path.is_file():
        errors.append(f"Missing: vscodium/{vscodium_exe}")

    # Extensions
    ext_parent = bundle_root / "vscodium" / "data" / "extensions"
    if ext_parent.is_dir():
        ext_dirs = list(ext_parent.glob("leanprover.lean4-*"))
        if not ext_dirs:
            errors.append("Missing lean4 extension in vscodium/data/extensions/")
    else:
        errors.append("Missing directory: vscodium/data/extensions/")

    # Settings
    settings_path = bundle_root / "vscodium" / "data" / "user-data" / "User" / "settings.json"
    if settings_path.is_file():
        try:
            settings = json.loads(settings_path.read_text())
            expected_keys = {
                "update.mode": "none",
                "extensions.autoUpdate": False,
                "telemetry.telemetryLevel": "off",
                "lean4.automaticallyBuildDependencies": False,
            }
            for key, expected in expected_keys.items():
                actual = settings.get(key)
                if actual != expected:
                    errors.append(f"settings.json: {key} = {actual!r}, expected {expected!r}")
        except json.JSONDecodeError as e:
            errors.append(f"settings.json: invalid JSON: {e}")
    else:
        errors.append("Missing: vscodium/data/user-data/User/settings.json")

    # --- Windows DLLs ---
    if is_windows:
        required_dlls = ["libleanshared.dll", "libInit_shared.dll", "libLake_shared.dll"]
        for dll in required_dlls:
            if not (bundle_root / "lean" / "bin" / dll).is_file():
                errors.append(f"Missing DLL: lean/bin/{dll}")

    # --- Git stubs ---
    manifest_path = bundle_root / "project" / "lake-manifest.json"
    if manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
            for pkg in manifest.get("packages", []):
                name = pkg["name"]
                rev = pkg.get("rev")
                if not rev:
                    continue
                pkg_dir = bundle_root / "project" / ".lake" / "packages" / name
                if not pkg_dir.is_dir():
                    errors.append(f"Missing package directory: .lake/packages/{name}")
                    continue
                head = pkg_dir / ".git" / "HEAD"
                if not head.is_file():
                    errors.append(f"Missing git stub: .lake/packages/{name}/.git/HEAD")
                else:
                    actual_rev = head.read_text().strip()
                    if actual_rev != rev:
                        errors.append(
                            f"Git stub mismatch for {name}: "
                            f"expected {rev[:12]}, got {actual_rev[:12]}"
                        )
        except json.JSONDecodeError as e:
            errors.append(f"lake-manifest.json: invalid JSON: {e}")

    # --- Olean spot check ---
    # Check that at least some oleans exist in package build dirs
    packages_dir = bundle_root / "project" / ".lake" / "packages"
    if packages_dir.is_dir():
        total_oleans = 0
        for pkg in packages_dir.iterdir():
            if pkg.is_dir():
                build_lib = pkg / ".lake" / "build" / "lib" / "lean"
                if build_lib.is_dir():
                    oleans = list(build_lib.rglob("*.olean"))
                    total_oleans += len(oleans)
        if total_oleans == 0:
            errors.append("No .olean files found in any package build directory")
        elif total_oleans < 100:
            errors.append(f"Suspiciously few oleans: {total_oleans} (expected hundreds)")

    return errors


def main():
    if len(sys.argv) < 2:
        print(f"Usage: {sys.argv[0]} <bundle_root> [--platform windows-x64]")
        sys.exit(1)

    bundle_root = Path(sys.argv[1])
    platform = "windows-x64"
    if "--platform" in sys.argv:
        idx = sys.argv.index("--platform")
        if idx + 1 < len(sys.argv):
            platform = sys.argv[idx + 1]

    errors = verify(bundle_root, platform)

    if errors:
        print(f"FAILED: {len(errors)} error(s) found:")
        for e in errors:
            print(f"  - {e}")
        sys.exit(1)
    else:
        print("OK: All structural checks passed")
        sys.exit(0)


if __name__ == "__main__":
    main()
