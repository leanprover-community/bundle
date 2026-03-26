"""Offline guarantee tests.

Runs Lake and the LSP server inside a Linux network namespace to prove
the bundle works with zero network access.

Two isolation methods are tried, in order:
1. ``unshare -rn`` — unprivileged, creates user+network namespace
2. ``sudo unshare --net`` — requires passwordless sudo (GitHub Actions)

Requires:
    - Linux (``unshare`` is Linux-only)
    - ``BUNDLE_ROOT`` environment variable pointing to an extracted bundle
    - One of the above isolation methods

In CI the preflight step should *fail* if neither method works rather
than silently skipping.  The ``skipif`` markers here are for local dev only.
"""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

BUNDLE_ROOT = os.environ.get("BUNDLE_ROOT")


def _detect_offline_cmd() -> list[str] | None:
    """Find a working network isolation command prefix.

    Returns the command prefix (e.g. ["unshare", "-rn", "--"]) or None.
    """
    if sys.platform != "linux":
        return None

    # Method 1: unprivileged user+network namespace
    try:
        r = subprocess.run(
            ["unshare", "-rn", "--", "true"],
            capture_output=True,
            timeout=5,
        )
        if r.returncode == 0:
            return ["unshare", "-rn", "--"]
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass

    # Method 2: sudo (passwordless) network namespace only
    if shutil.which("sudo"):
        try:
            r = subprocess.run(
                ["sudo", "-n", "unshare", "--net", "--", "true"],
                capture_output=True,
                timeout=5,
            )
            if r.returncode == 0:
                return ["sudo", "-n", "unshare", "--net", "--"]
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    return None


OFFLINE_CMD = _detect_offline_cmd()

skip_unless_offline = pytest.mark.skipif(
    not OFFLINE_CMD, reason="no network isolation available (tried unshare -rn, sudo unshare --net)"
)
skip_unless_bundle = pytest.mark.skipif(
    not BUNDLE_ROOT, reason="BUNDLE_ROOT not set"
)


def _bundle_root() -> Path:
    """Return the bundle root as a Path (call only when BUNDLE_ROOT is set)."""
    return Path(BUNDLE_ROOT)


def _build_lean_path(root: Path) -> str:
    """Replicate the LEAN_PATH logic from start_lean.sh."""
    entries = [
        str(root / "lean" / "lib" / "lean"),
        str(root / "project" / ".lake" / "build" / "lib" / "lean"),
    ]
    packages = root / "project" / ".lake" / "packages"
    if packages.is_dir():
        for pkg in sorted(packages.iterdir()):
            build_dir = pkg / ".lake" / "build" / "lib" / "lean"
            if build_dir.is_dir():
                entries.append(str(build_dir))
    return ":".join(entries)


def _clean_env(root: Path, tmp: str) -> dict[str, str]:
    """Build a sanitised environment for offline tests.

    Sets HOME, XDG_CACHE_HOME, and XDG_DATA_HOME to temp dirs so we
    can't accidentally succeed via host-level caches.
    """
    lean_path = _build_lean_path(root)
    return {
        "PATH": str(root / "lean" / "bin") + ":" + os.environ.get("PATH", ""),
        "LEAN_PATH": lean_path,
        "ELAN_HOME": str(root / "lean"),
        "HOME": tmp,
        "XDG_CACHE_HOME": os.path.join(tmp, ".cache"),
        "XDG_DATA_HOME": os.path.join(tmp, ".local/share"),
        # Lean needs these
        "TERM": os.environ.get("TERM", "dumb"),
    }


def _run_offline(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run *cmd* with network disabled via the detected isolation method."""
    assert OFFLINE_CMD, "no network isolation available"
    return subprocess.run(
        OFFLINE_CMD + cmd,
        **kwargs,
    )


# ---------------------------------------------------------------------------
# Negative control: prove the isolation harness actually blocks network
# ---------------------------------------------------------------------------

@skip_unless_offline
def test_offline_actually_blocks_network() -> None:
    """Verify that unshare -rn prevents network access."""
    r = _run_offline(
        [
            sys.executable, "-c",
            "import urllib.request; urllib.request.urlopen('https://example.com')",
        ],
        capture_output=True,
        timeout=15,
    )
    assert r.returncode != 0, (
        "Network request succeeded inside unshare -rn — isolation is broken"
    )
    assert b"Network is unreachable" in r.stderr or b"URLError" in r.stderr, (
        f"Unexpected failure mode: {r.stderr[-500:]}"
    )


# ---------------------------------------------------------------------------
# Tier 2 offline: lake build --no-build
# ---------------------------------------------------------------------------

@skip_unless_offline
@skip_unless_bundle
def test_lake_no_build_offline() -> None:
    """Lake must load the workspace without network errors.

    ``lake build --no-build`` may report targets as out-of-date (exit 3)
    because Lake's trace-based freshness check sees different hashes after
    copy/zip/unzip.  That's acceptable — it means "stale but not rebuilding",
    not "tried to fetch and failed."

    What we actually verify: no git/network errors in the output.
    """
    root = _bundle_root()
    lake = root / "lean" / "bin" / "lake"
    assert lake.is_file(), f"lake not found: {lake}"

    with tempfile.TemporaryDirectory(prefix="offline-lake-") as tmp:
        env = _clean_env(root, tmp)
        r = _run_offline(
            [str(lake), "build", "--no-build"],
            capture_output=True,
            timeout=120,
            cwd=str(root / "project"),
            env=env,
        )

    stdout = r.stdout.decode("utf-8", errors="replace")
    stderr = r.stderr.decode("utf-8", errors="replace")
    combined = stdout + stderr

    # Network/git errors indicate the manifest rewrite missed something
    network_errors = [
        line for line in combined.splitlines()
        if any(s in line.lower() for s in [
            "network", "git clone", "git fetch", "repository not found",
            "could not resolve host", "connection refused",
        ])
    ]
    assert not network_errors, (
        f"Lake attempted network access offline:\n"
        + "\n".join(f"  {l}" for l in network_errors)
    )

    # Exit code 0 = all fresh, 3 = stale but not rebuilding.  Both are OK.
    assert r.returncode in (0, 3), (
        f"lake build --no-build failed unexpectedly (rc={r.returncode})\n"
        f"stdout: {stdout[:2000]}\n"
        f"stderr: {stderr[:2000]}"
    )


# ---------------------------------------------------------------------------
# Tier 3 offline: LSP smoke test
# ---------------------------------------------------------------------------

@skip_unless_offline
@skip_unless_bundle
def test_lsp_offline() -> None:
    """The Lean language server must start and produce diagnostics offline."""
    root = _bundle_root()

    # Find a .lean file in the project
    project = root / "project"
    lean_files = [
        f for f in project.rglob("*.lean")
        if ".lake" not in f.parts and "lakefile" not in f.name.lower()
    ]
    assert lean_files, "No .lean files found in project"
    lean_file = lean_files[0]

    with tempfile.TemporaryDirectory(prefix="offline-lsp-") as tmp:
        env = _clean_env(root, tmp)
        # Use the existing lsp_smoke module
        test_script = Path(__file__).parent / "lsp_smoke.py"
        r = _run_offline(
            [
                sys.executable, str(test_script),
                str(root / "lean" / "bin"),
                env["LEAN_PATH"],
                str(lean_file),
                "--timeout", "90",
            ],
            capture_output=True,
            timeout=120,
            env=env,
        )

    stdout = r.stdout.decode("utf-8", errors="replace")
    stderr = r.stderr.decode("utf-8", errors="replace")
    assert r.returncode == 0, (
        f"LSP smoke test failed offline (rc={r.returncode})\n"
        f"stdout: {stdout[:2000]}\n"
        f"stderr: {stderr[:2000]}"
    )
