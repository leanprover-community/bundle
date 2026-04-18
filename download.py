"""Download components for the Lean 4 bundle.

Downloads the Lean toolchain, VSCodium portable, and lean4 VS Code extension.
"""

import calendar
import hashlib
import io
import json
import os
import shutil
import socket
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.request
import zipfile
from pathlib import Path


PLATFORM_MAP = {
    "windows": {
        "lean_suffix": "windows",
        "vscodium_asset_pattern": "VSCodium-win32-x64-{version}.zip",
        "vscodium_extract": "zip",
    },
    "linux-x64": {
        "lean_suffix": "linux",
        "vscodium_asset_pattern": "VSCodium-linux-x64-{version}.tar.gz",
        "vscodium_extract": "tar.gz",
    },
    "linux-arm64": {
        "lean_suffix": "linux_aarch64",
        "vscodium_asset_pattern": "VSCodium-linux-arm64-{version}.tar.gz",
        "vscodium_extract": "tar.gz",
    },
    "darwin-x64": {
        "lean_suffix": "darwin",
        "vscodium_asset_pattern": "VSCodium-darwin-x64-{version}.zip",
        "vscodium_extract": "zip",
    },
    "darwin-arm64": {
        "lean_suffix": "darwin_aarch64",
        "vscodium_asset_pattern": "VSCodium-darwin-arm64-{version}.zip",
        "vscodium_extract": "zip",
    },
}


# Candidate C compilers that can produce a 64-bit Windows PE. Each entry is
# ``(binary, extra_args, cross_only)`` — ``cross_only`` binaries are explicit
# cross-compilers and are always safe. Non-``cross_only`` entries are native
# compilers that are only safe when we are already running on Windows (where
# ``gcc``/``clang`` produce PE binaries without extra flags). Running
# ``bundle.py --platform windows`` on Linux/macOS without mingw or zig
# would otherwise silently produce an ELF/Mach-O file named ``git.exe``.
_GIT_SHIM_COMPILERS: tuple[tuple[str, tuple[str, ...], bool], ...] = (
    # Explicit cross-compilers — always safe.
    ("x86_64-w64-mingw32-gcc", (), True),
    ("x86_64-w64-mingw32-cc", (), True),
    # Zig's cc driver ships its own libc and cross-compiles trivially.
    ("zig", ("cc", "-target", "x86_64-windows-gnu"), True),
    # Native compilers — only trusted on a Windows build host.
    ("gcc", (), False),
    ("clang", (), False),
)

# Extension dependencies expected from the lean4 extension's package.json.
# Maps extension ID to pinned version. Reject unknown IDs and fetch only the
# pinned version to prevent dependency injection via a compromised extension.
ALLOWED_EXTENSION_DEPS = {
    "tamasfe.even-better-toml": "0.21.2",
}


def _sha256_file(path: Path) -> str:
    """Compute the SHA-256 hex digest of a file."""
    with open(path, "rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def _safe_extract_zip(zf: zipfile.ZipFile, dest: Path) -> None:
    """Extract a zip file, preserving symlinks and rejecting path traversal."""
    dest = dest.resolve()

    # Reject duplicate entries (could pair one entry's mode with another's content)
    seen: set[str] = set()
    for info in zf.infolist():
        normalized = info.filename.rstrip("/")
        if normalized in seen:
            raise ValueError(f"Duplicate zip entry: {info.filename!r}")
        seen.add(normalized)
        if not (dest / info.filename).resolve().is_relative_to(dest):
            raise ValueError(f"Zip entry would extract outside {dest}: {info.filename!r}")

    for info in zf.infolist():
        out_path = dest / info.filename
        # Check if this entry is a Unix symlink (mode & S_IFLNK)
        unix_mode = info.external_attr >> 16
        if (unix_mode & 0o170000) == 0o120000:
            # Symlink: read via ZipInfo object (not filename) to avoid
            # ambiguity if entries were duplicated.
            link_target = zf.read(info).decode("utf-8")
            # Validate the resolved symlink stays inside dest
            resolved = (out_path.parent / link_target).resolve()
            if not resolved.is_relative_to(dest):
                raise ValueError(
                    f"Symlink would point outside {dest}: {info.filename!r} -> {link_target!r}"
                )
            out_path.parent.mkdir(parents=True, exist_ok=True)
            if out_path.exists() or out_path.is_symlink():
                out_path.unlink()
            out_path.symlink_to(link_target)
        elif info.is_dir():
            out_path.mkdir(parents=True, exist_ok=True)
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(out_path, "wb") as dst:
                shutil.copyfileobj(src, dst)
            # Restore the stored timestamp so olean freshness survives
            mtime = calendar.timegm(info.date_time + (0, 0, -1))
            os.utime(out_path, (mtime, mtime))
            # Restore Unix permissions if stored
            if unix_mode:
                out_path.chmod(unix_mode & 0o777)


def _safe_extract_tar(tf: tarfile.TarFile, dest: Path) -> None:
    """Extract a tar file safely.

    Uses the 'data' filter on Python 3.12+ which rejects absolute paths,
    paths with .., and special file types. On 3.11, validates manually.
    """
    if sys.version_info >= (3, 12):
        tf.extractall(dest, filter="data")
        return
    dest = dest.resolve()
    for member in tf.getmembers():
        if not (dest / member.name).resolve().is_relative_to(dest):
            raise ValueError(f"Tar entry would extract outside {dest}: {member.name!r}")
        if member.issym() or member.islnk():
            if not (dest / member.linkname).resolve().is_relative_to(dest):
                raise ValueError(f"Tar link points outside {dest}: {member.name!r}")
    tf.extractall(dest)


def build_git_shim(dest_dir: Path, platform: str) -> Path | None:
    """Build a tiny ``git.exe`` shim for Windows bundles.

    The lean4 VS Code extension and VS Code's built-in git extension both
    probe for ``git`` on PATH at startup. Historically the bundle shipped
    MinGit (~46 MB) to satisfy this probe. The shim is a ~30 KB C program
    that answers only the probes both extensions perform at activation
    (see ``shim/git_shim.c`` for the full probe surface).

    Returns the path to the built ``git.exe``, or ``None`` on non-Windows
    platforms. Raises ``RuntimeError`` if no suitable compiler is found;
    on Linux, install ``gcc-mingw-w64-x86-64`` or put ``zig`` on PATH.
    Raises ``RuntimeError`` (with the offending bytes) if the compiler
    produced something that isn't a Windows PE image — this guards
    against accidentally building with a native compiler that doesn't
    cross-compile (e.g. clang on macOS without ``-target``).
    """
    if not platform.startswith("windows"):
        return None

    source = Path(__file__).resolve().parent / "shim" / "git_shim.c"
    if not source.is_file():
        raise RuntimeError(f"git shim source not found: {source}")

    shim_dir = dest_dir / "git-shim"
    shim_dir.mkdir(parents=True, exist_ok=True)
    output = shim_dir / "git.exe"
    # Source is unlikely to change mid-build, but if someone edits the .c
    # and reuses an existing work-dir we want the new shim, not a stale one.
    if output.is_file() and output.stat().st_mtime >= source.stat().st_mtime:
        _assert_pe_image(output)
        return output

    compiler, extra = _find_git_shim_compiler()
    cmd = [compiler, *extra, "-O2", "-Wall", "-Wextra", "-s",
           "-o", str(output), str(source)]
    print(f"  Building git shim with {compiler}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"git shim build failed ({' '.join(cmd)}):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    _assert_pe_image(output)
    size = output.stat().st_size
    print(f"  Built {output.name} ({size} bytes)")
    return output


def _assert_pe_image(path: Path) -> None:
    """Verify ``path`` is a Windows PE32+ image.

    A PE file begins with a DOS header (``MZ``) followed by an optional
    DOS stub; the real PE header lives at the 4-byte offset stored at
    0x3C in the DOS header and starts with ``PE\\0\\0``. We check both so
    an accidentally native-compiled ELF/Mach-O binary is caught loudly
    instead of silently shipping a non-Windows file named ``git.exe``.
    """
    with open(path, "rb") as f:
        dos = f.read(0x40)
        if len(dos) < 0x40 or dos[:2] != b"MZ":
            raise RuntimeError(
                f"git shim at {path} is not a PE image (bad DOS magic): "
                f"{dos[:4]!r}"
            )
        e_lfanew = int.from_bytes(dos[0x3C:0x40], "little")
        f.seek(e_lfanew)
        pe_sig = f.read(4)
        if pe_sig != b"PE\x00\x00":
            raise RuntimeError(
                f"git shim at {path} is not a PE image "
                f"(bad PE signature at 0x{e_lfanew:X}): {pe_sig!r}. "
                f"A native non-Windows compiler probably built the shim. "
                f"Install mingw-w64 (apt-get install gcc-mingw-w64-x86-64) "
                f"or zig."
            )


def _find_git_shim_compiler() -> tuple[str, tuple[str, ...]]:
    """Return ``(binary, extra_args)`` for the first available compiler.

    On non-Windows build hosts, only explicit cross-compilers are
    considered — a native ``gcc``/``clang`` on Linux/macOS would produce
    an ELF/Mach-O binary rather than a PE image. Raises ``RuntimeError``
    with install instructions if none is found.
    """
    on_windows = sys.platform == "win32"
    for binary, extra, cross_only in _GIT_SHIM_COMPILERS:
        if not cross_only and not on_windows:
            continue
        resolved = shutil.which(binary)
        if resolved:
            return resolved, extra
    raise RuntimeError(
        "No C compiler found for building the Windows git shim.\n"
        "\n"
        "Install zig (easiest cross-platform option):\n"
        "  macOS:   brew install zig\n"
        "  Linux:   apt-get install zig\n"
        "  Windows: winget install zig.zig\n"
        "\n"
        "Or install mingw-w64:\n"
        "  macOS:   brew install mingw-w64\n"
        "  Linux:   apt-get install gcc-mingw-w64-x86-64\n"
        "  Windows: https://www.msys2.org/"
    )


def parse_toolchain(toolchain_file: Path) -> str:
    """Parse the lean-toolchain file and return the version string.

    E.g. "leanprover/lean4:v4.26.0" -> "v4.26.0"
    """
    content = toolchain_file.read_text().strip()
    if ":" in content:
        return content.split(":")[-1]
    return content


def _download(url: str, dest: Path, retries: int = 3) -> None:
    """Download a URL to a local file with retries on transient errors."""
    for attempt in range(1, retries + 1):
        try:
            print(f"  Downloading {url}")
            req = urllib.request.Request(url, headers={"User-Agent": "lean-bundle/1.0"})
            with urllib.request.urlopen(req, timeout=30) as resp, open(dest, "wb") as f:
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
            return
        except (urllib.error.URLError, socket.timeout, ConnectionError) as e:
            if isinstance(e, urllib.error.HTTPError) and e.code < 500:
                raise  # don't retry client errors (4xx)
            if dest.exists():
                dest.unlink()
            if attempt == retries:
                raise
            delay = 2 ** (attempt + 1)
            print(f"  Retry {attempt}/{retries - 1} after {delay}s: {e}")
            time.sleep(delay)


def _download_bytes(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "lean-bundle/1.0"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read()


def download_lean_toolchain(version: str, platform: str, dest_dir: Path) -> Path:
    """Download, extract, and return the path to the Lean toolchain (containing bin/, lib/)."""
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
    print(f"  SHA-256: {_sha256_file(archive_path)}")

    print(f"  Extracting {archive_name}...")
    lean_dir = dest_dir / "lean-extract"
    lean_dir.mkdir(exist_ok=True)

    if archive_name.endswith(".zip"):
        with zipfile.ZipFile(archive_path) as zf:
            _safe_extract_zip(zf, lean_dir)
    elif archive_name.endswith(".tar.zst"):
        # macOS bsdtar < libarchive 3.6 doesn't support zstd.  If tar can't
        # open the .tar.zst directly, decompress with the `zstd` CLI first.
        tar_source = archive_path
        try:
            result = subprocess.run(
                ["tar", "--list", "-f", str(archive_path)],
                capture_output=True, text=True, check=True,
            )
        except subprocess.CalledProcessError:
            if shutil.which("zstd") is None:
                raise RuntimeError(
                    f"tar cannot read {archive_name} (no zstd support) and "
                    f"the `zstd` command is not installed.  Install zstd "
                    f"(e.g. `brew install zstd`) or use a newer tar/libarchive."
                )
            tar_source = archive_path.with_suffix("")  # strip .zst
            subprocess.run(
                ["zstd", "-d", "-f", str(archive_path), "-o", str(tar_source)],
                check=True,
            )
            archive_path.unlink()
            result = subprocess.run(
                ["tar", "--list", "-f", str(tar_source)],
                capture_output=True, text=True, check=True,
            )
        # Validate archive members before extracting (tar xf doesn't reject ..)
        resolved_dest = lean_dir.resolve()
        for member in result.stdout.splitlines():
            if not (resolved_dest / member).resolve().is_relative_to(resolved_dest):
                raise ValueError(f"Tar entry would extract outside {lean_dir}: {member!r}")
        subprocess.run(
            ["tar", "xf", str(tar_source), "-C", str(lean_dir)],
            check=True,
        )
        if tar_source != archive_path:
            tar_source.unlink()
    elif archive_name.endswith(".tar.gz"):
        with tarfile.open(archive_path) as tf:
            _safe_extract_tar(tf, lean_dir)

    archive_path.unlink(missing_ok=True)

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


def download_vscodium(platform: str, dest_dir: Path, version: str | None = None) -> tuple[Path, str]:
    """Download and extract VSCodium portable. Uses latest version if none specified.

    Returns (path_to_vscodium_dir, resolved_version).
    """
    if version is None:
        version = _get_latest_vscodium_version()
        print(f"  Using VSCodium version: {version}")

    plat = PLATFORM_MAP[platform]
    asset_name = plat["vscodium_asset_pattern"].format(version=version)
    url = f"https://github.com/VSCodium/vscodium/releases/download/{version}/{asset_name}"

    archive_path = dest_dir / asset_name
    _download(url, archive_path)
    print(f"  SHA-256: {_sha256_file(archive_path)}")

    print(f"  Extracting {asset_name}...")
    vscodium_dir = dest_dir / "vscodium"
    vscodium_dir.mkdir(exist_ok=True)

    if plat["vscodium_extract"] == "zip":
        with zipfile.ZipFile(archive_path) as zf:
            _safe_extract_zip(zf, vscodium_dir)
    else:
        with tarfile.open(archive_path) as tf:
            _safe_extract_tar(tf, vscodium_dir)

    archive_path.unlink()
    return vscodium_dir, version


def _get_latest_lean4_extension_version() -> str:
    """Get the latest lean4 VS Code extension version from GitHub releases."""
    url = "https://api.github.com/repos/leanprover/vscode-lean4/releases/latest"
    data = json.loads(_download_bytes(url))
    return data["tag_name"].lstrip("v")


def _extract_vsix(vsix_path: Path, ext_dir: Path) -> None:
    """Extract a VSIX archive to a directory.

    A VSIX is a zip with an extension/ subdirectory containing the actual
    extension files. VS Code expects package.json at the extension root,
    so we extract extension/* to the root, discarding the VSIX metadata.
    """
    ext_dir.mkdir(exist_ok=True)
    resolved_dest = ext_dir.resolve()
    with zipfile.ZipFile(vsix_path) as zf:
        prefix = "extension/"
        for info in zf.infolist():
            if info.filename.startswith(prefix):
                info.filename = info.filename[len(prefix):]
                if info.filename:
                    target = (resolved_dest / info.filename).resolve()
                    if not target.is_relative_to(resolved_dest):
                        raise ValueError(
                            f"VSIX entry would extract outside {ext_dir}: {info.filename!r}"
                        )
                    zf.extract(info, ext_dir)
    vsix_path.unlink()


def download_openvsx_extension(
    publisher: str, name: str, dest_dir: Path, version: str | None = None
) -> Path:
    """Download a VS Code extension from Open VSX. Uses latest version if none specified."""
    ext_id = f"{publisher}.{name}"

    if version is None:
        url = f"https://open-vsx.org/api/{publisher}/{name}/latest"
        data = json.loads(_download_bytes(url))
        version = data["version"]

    print(f"  Downloading {ext_id} v{version} from Open VSX...")
    vsix_url = f"https://open-vsx.org/api/{publisher}/{name}/{version}/file/{ext_id}-{version}.vsix"
    vsix_path = dest_dir / f"{ext_id}-{version}.vsix"
    _download(vsix_url, vsix_path)
    print(f"  SHA-256: {_sha256_file(vsix_path)}")

    ext_dir = dest_dir / f"{ext_id}-{version}"
    print(f"  Extracting {ext_id}...")
    _extract_vsix(vsix_path, ext_dir)

    return ext_dir


def download_lean4_extension(
    dest_dir: Path, version: str | None = None
) -> tuple[list[Path], str]:
    """Download the lean4 extension and its dependencies.

    Returns (extension_dirs, resolved_version).
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
        url = f"https://open-vsx.org/api/leanprover/lean4/{version}/file/leanprover.lean4-{version}.vsix"
        _download(url, vsix_path)
    print(f"  SHA-256: {_sha256_file(vsix_path)}")

    ext_dir = dest_dir / f"leanprover.lean4-{version}"
    print(f"  Extracting lean4 extension...")
    _extract_vsix(vsix_path, ext_dir)

    extension_dirs = [ext_dir]

    # Download extension dependencies (only from allowlist, at pinned versions)
    pkg_path = ext_dir / "package.json"
    if pkg_path.is_file():
        pkg = json.loads(pkg_path.read_text())
        for dep_id in pkg.get("extensionDependencies", []):
            pinned_version = ALLOWED_EXTENSION_DEPS.get(dep_id)
            if pinned_version is None:
                raise ValueError(
                    f"Unexpected extension dependency {dep_id!r}. "
                    f"Update ALLOWED_EXTENSION_DEPS in download.py if this is intentional."
                )
            parts = dep_id.split(".", 1)
            if len(parts) == 2:
                dep_dir = download_openvsx_extension(
                    parts[0], parts[1], dest_dir, version=pinned_version
                )
                extension_dirs.append(dep_dir)

    return extension_dirs, version


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
            if f.is_file() or f.is_symlink():
                name = f.name.lower()
                if any(name.startswith(p) for p in ["libllvm", "libclang", "liblld"]):
                    f.unlink()
                elif f.is_file() and name.endswith(".a"):
                    f.unlink()

    # Remove static libraries under lib/lean/
    lean_lib = lib_dir / "lean"
    if lean_lib.is_dir():
        for f in lean_lib.rglob("*.a"):
            f.unlink()
