"""Download components for the Lean 4 bundle.

Downloads the Lean toolchain, VSCodium portable, and lean4 VS Code extension.
"""

import io
import json
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from pathlib import Path


PLATFORM_MAP = {
    "windows-x64": {
        "lean_suffix": "windows",
        "vscodium_asset_pattern": "VSCodium-win32-x64-{version}.zip",
        "vscodium_extract": "zip",
    },
    "linux-x64": {
        "lean_suffix": "linux",
        "vscodium_asset_pattern": "VSCodium-linux-x64-{version}.tar.gz",
        "vscodium_extract": "tar.gz",
    },
    "darwin-x64": {
        "lean_suffix": "macOS",
        "vscodium_asset_pattern": "VSCodium-darwin-x64-{version}.zip",
        "vscodium_extract": "zip",
    },
    "darwin-arm64": {
        "lean_suffix": "macOS_aarch64",
        "vscodium_asset_pattern": "VSCodium-darwin-arm64-{version}.zip",
        "vscodium_extract": "zip",
    },
}


def parse_toolchain(toolchain_file: Path) -> str:
    """Parse the lean-toolchain file and return the version string.

    E.g. "leanprover/lean4:v4.26.0" -> "v4.26.0"
    """
    content = toolchain_file.read_text().strip()
    if ":" in content:
        return content.split(":")[-1]
    return content


def _download(url: str, dest: Path) -> None:
    """Download a URL to a local file with progress indication."""
    print(f"  Downloading {url}")
    req = urllib.request.Request(url, headers={"User-Agent": "lean-bundle/1.0"})
    with urllib.request.urlopen(req) as resp, open(dest, "wb") as f:
        total = resp.headers.get("Content-Length")
        downloaded = 0
        while True:
            chunk = resp.read(1024 * 1024)
            if not chunk:
                break
            f.write(chunk)
            downloaded += len(chunk)
            if total:
                pct = downloaded * 100 // int(total)
                print(f"\r  {downloaded // (1024*1024)}MB / {int(total) // (1024*1024)}MB ({pct}%)", end="", flush=True)
        if total:
            print()


def _download_bytes(url: str) -> bytes:
    """Download a URL and return its content as bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": "lean-bundle/1.0"})
    with urllib.request.urlopen(req) as resp:
        return resp.read()


def download_lean_toolchain(version: str, platform: str, dest_dir: Path) -> Path:
    """Download and extract the Lean toolchain.

    Args:
        version: Lean version, e.g. "v4.26.0"
        platform: Target platform key (e.g. "windows-x64")
        dest_dir: Directory to extract into.

    Returns:
        Path to the extracted lean directory (containing bin/, lib/).
    """
    plat = PLATFORM_MAP[platform]
    version_short = version.lstrip("v")
    lean_suffix = plat["lean_suffix"]

    if platform.startswith("windows"):
        url = f"https://github.com/leanprover/lean4/releases/download/{version}/lean-{version_short}-{lean_suffix}.zip"
    else:
        url = f"https://github.com/leanprover/lean4/releases/download/{version}/lean-{version_short}-{lean_suffix}.tar.zst"

    archive_name = url.split("/")[-1]
    archive_path = dest_dir / archive_name

    _download(url, archive_path)

    print(f"  Extracting {archive_name}...")
    lean_dir = dest_dir / "lean-extract"
    lean_dir.mkdir(exist_ok=True)

    if archive_name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(lean_dir)
    elif archive_name.endswith(".tar.zst"):
        # Use tar command which handles zstd
        subprocess.run(
            ["tar", "xf", str(archive_path), "-C", str(lean_dir)],
            check=True,
        )
    elif archive_name.endswith(".tar.gz"):
        with tarfile.open(archive_path) as tf:
            tf.extractall(lean_dir)

    archive_path.unlink()

    # The archive typically extracts to a subdirectory like lean-4.26.0-linux/
    subdirs = [d for d in lean_dir.iterdir() if d.is_dir()]
    if len(subdirs) == 1:
        return subdirs[0]
    return lean_dir


def _get_latest_vscodium_version() -> str:
    """Get the latest VSCodium release version."""
    url = "https://api.github.com/repos/VSCodium/vscodium/releases/latest"
    data = json.loads(_download_bytes(url))
    return data["tag_name"]


def download_vscodium(platform: str, dest_dir: Path, version: str | None = None) -> Path:
    """Download and extract VSCodium portable.

    Args:
        platform: Target platform key.
        dest_dir: Directory to extract into.
        version: Optional VSCodium version; uses latest if None.

    Returns:
        Path to the extracted VSCodium directory.
    """
    if version is None:
        version = _get_latest_vscodium_version()
        print(f"  Using VSCodium version: {version}")

    plat = PLATFORM_MAP[platform]
    asset_name = plat["vscodium_asset_pattern"].format(version=version)
    url = f"https://github.com/VSCodium/vscodium/releases/download/{version}/{asset_name}"

    archive_path = dest_dir / asset_name
    _download(url, archive_path)

    print(f"  Extracting {asset_name}...")
    vscodium_dir = dest_dir / "vscodium"
    vscodium_dir.mkdir(exist_ok=True)

    if plat["vscodium_extract"] == "zip":
        with zipfile.ZipFile(archive_path) as zf:
            zf.extractall(vscodium_dir)
    else:
        with tarfile.open(archive_path) as tf:
            tf.extractall(vscodium_dir)

    archive_path.unlink()
    return vscodium_dir


def _get_latest_lean4_extension_version() -> str:
    """Get the latest lean4 VS Code extension version from GitHub releases."""
    url = "https://api.github.com/repos/leanprover/vscode-lean4/releases/latest"
    data = json.loads(_download_bytes(url))
    return data["tag_name"].lstrip("v")


def download_lean4_extension(dest_dir: Path, version: str | None = None) -> Path:
    """Download and extract the lean4 VS Code extension.

    Args:
        dest_dir: Directory to extract the extension into.
        version: Optional version string; uses latest if None.

    Returns:
        Path to the extracted extension directory.
    """
    if version is None:
        version = _get_latest_lean4_extension_version()
        print(f"  Using lean4 extension version: {version}")

    # Try GitHub releases first, fall back to Open VSX
    url = f"https://github.com/leanprover/vscode-lean4/releases/download/v{version}/lean4-{version}.vsix"

    vsix_path = dest_dir / f"lean4-{version}.vsix"
    try:
        _download(url, vsix_path)
    except urllib.error.HTTPError:
        # Fall back to Open VSX
        url = f"https://open-vsx.org/api/leanprover/lean4/{version}/file/leanprover.lean4-{version}.vsix"
        _download(url, vsix_path)

    # A VSIX is just a zip file
    ext_dir = dest_dir / f"leanprover.lean4-{version}"
    ext_dir.mkdir(exist_ok=True)

    print(f"  Extracting lean4 extension...")
    with zipfile.ZipFile(vsix_path) as zf:
        zf.extractall(ext_dir)

    vsix_path.unlink()

    return ext_dir


def trim_lean_toolchain(lean_dir: Path, platform: str) -> None:
    """Remove unnecessary files from the lean toolchain to reduce bundle size.

    Keeps: lean, lake, runtime shared libraries, Init/Lean/Std/Lake oleans.
    Removes: clang, lld, LLVM libs, static libraries, include, src, share.
    """
    is_windows = platform.startswith("windows")
    bin_dir = lean_dir / "bin"
    lib_dir = lean_dir / "lib"

    # On Windows, DLLs are in bin/ alongside executables.
    # We must keep lean.exe, lake.exe, and all required DLLs.
    remove_bin_patterns = [
        "cadical", "clang", "leanc", "leanmake",
        "ld.lld", "lld", "llvm-ar",
        # DLLs we don't need
        "libllvm", "libclang", "liblld",
    ]

    if bin_dir.is_dir():
        for f in list(bin_dir.iterdir()):
            if not f.is_file():
                continue
            name = f.name.lower()
            stem = f.stem.lower()
            if any(stem == p or stem.startswith(p + ".") or name.startswith(p)
                   for p in remove_bin_patterns):
                f.unlink()

    # Remove unwanted lib directories and files
    remove_dirs = ["clang", "glibc"]
    for d in remove_dirs:
        p = lib_dir / d
        if p.is_dir():
            shutil.rmtree(p)

    # Remove directories not under lib/lean/
    remove_top = ["include", "src", "share"]
    for d in remove_top:
        p = lean_dir / d
        if p.is_dir():
            shutil.rmtree(p)

    # Remove LLVM/clang shared libraries and static libraries
    if lib_dir.is_dir():
        for f in list(lib_dir.iterdir()):
            if f.is_file():
                name = f.name.lower()
                if any(name.startswith(p) for p in ["libllvm", "libclang", "liblld"]):
                    f.unlink()
                elif name.endswith(".a"):
                    f.unlink()

    # Remove static libraries under lib/lean/
    lean_lib = lib_dir / "lean"
    if lean_lib.is_dir():
        for f in lean_lib.rglob("*.a"):
            f.unlink()
