"""Tests for the tiny git.exe shim that replaces MinGit on Windows.

These tests compile the C source with whatever native compiler is on PATH
and run it directly. That covers the behavioural surface (correct exit
codes, version string, stderr phrasing) independent of the Windows PE
cross-build used in production. The production build itself is exercised
end-to-end by the Tier 1 bundle verify check and the Tier 5/6 Playwright
runs on Windows CI.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM_SOURCE = REPO_ROOT / "shim" / "git_shim.c"


def _native_cc() -> str | None:
    """First available native C compiler."""
    for name in ("gcc", "clang", "cc"):
        p = shutil.which(name)
        if p:
            return p
    return None


@pytest.fixture(scope="module")
def shim_binary(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Build the shim once per test module with the native compiler."""
    cc = _native_cc()
    if cc is None:
        pytest.skip("no native C compiler available")
    if not SHIM_SOURCE.is_file():
        pytest.fail(f"shim source missing: {SHIM_SOURCE}")

    out_dir = tmp_path_factory.mktemp("shim")
    exe_name = "git.exe" if sys.platform == "win32" else "git_shim"
    out = out_dir / exe_name

    result = subprocess.run(
        [cc, "-O2", "-Wall", "-Wextra", "-o", str(out), str(SHIM_SOURCE)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        pytest.fail(
            f"Failed to build shim with {cc}:\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    if not out.is_file():
        pytest.fail(f"shim binary not produced at {out}")
    # Some filesystems drop the exec bit on write; make sure we can run it.
    if sys.platform != "win32":
        os.chmod(out, 0o755)
    return out


def _run(shim: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(shim), *args],
        capture_output=True,
        text=True,
        timeout=10,
    )


class TestVersionProbe:
    """The most important probe: the one both extensions gate activation on."""

    def test_dash_dash_version_exits_zero(self, shim_binary: Path) -> None:
        r = _run(shim_binary, "--version")
        assert r.returncode == 0, f"stderr: {r.stderr}"

    def test_dash_dash_version_prints_git_version(self, shim_binary: Path) -> None:
        r = _run(shim_binary, "--version")
        assert r.stdout.startswith("git version "), f"stdout: {r.stdout!r}"

    def test_version_subcommand_exits_zero(self, shim_binary: Path) -> None:
        """`git version` (no dashes) is a real alias and must behave."""
        r = _run(shim_binary, "version")
        assert r.returncode == 0
        assert r.stdout.startswith("git version ")

    def test_dash_v_exits_zero(self, shim_binary: Path) -> None:
        r = _run(shim_binary, "-v")
        assert r.returncode == 0
        assert r.stdout.startswith("git version ")

    def test_reported_version_is_modern(self, shim_binary: Path) -> None:
        """VS Code's git extension pops a modal warning for git < 2 or
        git == 2.25.x / 2.26.x. The shim must not trip either check."""
        r = _run(shim_binary, "--version")
        version = r.stdout.removeprefix("git version ").strip()
        major_s, minor_s, *_ = version.split(".")
        major = int(major_s)
        minor = int(minor_s)
        assert major >= 2, f"version {version} triggers git-v1 modal"
        assert not (major == 2 and minor in (25, 26)), (
            f"version {version} triggers the Windows 2.25/2.26 modal"
        )


class TestNotARepoProbe:
    """VS Code's built-in git extension calls `rev-parse --show-toplevel`
    on every workspace folder and expects non-zero + a /Not a git
    repository/i stderr to treat the folder as a non-repo."""

    def test_rev_parse_exits_nonzero(self, shim_binary: Path) -> None:
        r = _run(shim_binary, "rev-parse", "--show-toplevel")
        assert r.returncode != 0

    def test_rev_parse_stderr_matches_git_error_code_regex(
        self, shim_binary: Path
    ) -> None:
        """VS Code's getGitErrorCode maps stderr matching
        /Not a git repository/i onto GitErrorCodes.NotAGitRepository."""
        r = _run(shim_binary, "rev-parse", "--show-toplevel")
        assert "not a git repository" in r.stderr.lower(), (
            f"stderr does not match /Not a git repository/i: {r.stderr!r}"
        )

    def test_rev_parse_exit_code_128(self, shim_binary: Path) -> None:
        """Real git returns 128 for "not a git repository"."""
        r = _run(shim_binary, "rev-parse", "--show-toplevel")
        assert r.returncode == 128

    def test_toplevel_flag_c_is_consumed(self, shim_binary: Path) -> None:
        """`git -C <dir> rev-parse` is common; the `-C <dir>` must not be
        mistaken for the subcommand."""
        r = _run(shim_binary, "-C", "/tmp", "rev-parse", "--show-toplevel")
        assert r.returncode == 128
        assert "not a git repository" in r.stderr.lower()

    def test_toplevel_flag_c_config_is_consumed(self, shim_binary: Path) -> None:
        """`git -c user.name=foo commit` — the `-c user.name=foo` must
        not be mistaken for the subcommand."""
        r = _run(
            shim_binary,
            "-c", "user.name=foo",
            "-c", "user.email=",
            "commit", "-m", "x",
        )
        assert r.returncode == 128
        assert "not a git repository" in r.stderr.lower()

    def test_dangling_dash_c_does_not_crash(self, shim_binary: Path) -> None:
        """`git -C` with no following arg — the scanner must not read
        past argc or dereference a nonexistent argv entry."""
        r = _run(shim_binary, "-C")
        assert r.returncode == 128
        assert "not a git repository" in r.stderr.lower()

    def test_bare_git_does_not_crash(self, shim_binary: Path) -> None:
        """Bare `git` with no args is degenerate but must not crash."""
        r = _run(shim_binary)
        assert r.returncode == 128
        assert "not a git repository" in r.stderr.lower()


class TestUnknownSubcommands:
    """Anything the shim doesn't explicitly handle must be safe to fail.
    Both VS Code extensions and Lake wrap these in try/catch or `captureProc?`."""

    @pytest.mark.parametrize(
        "argv",
        [
            ("status", "--porcelain"),
            ("rev-parse", "HEAD"),
            ("rev-parse", "--verify", "--end-of-options", "HEAD"),
            ("config", "--get", "user.name"),
            ("remote", "get-url", "origin"),
            ("describe", "--tags", "--exact-match", "HEAD"),
            ("clone", "https://example.com/x", "/tmp/x"),
            ("fetch", "--tags", "--force", "origin"),
            ("checkout", "--detach", "abc123", "--"),
            ("diff", "HEAD", "--exit-code"),
        ],
    )
    def test_unknown_subcommand_exits_128(
        self, shim_binary: Path, argv: tuple[str, ...]
    ) -> None:
        r = _run(shim_binary, *argv)
        assert r.returncode == 128, (
            f"`git {' '.join(argv)}` exited {r.returncode}, expected 128"
        )


class TestBuildGitShim:
    """Exercise the bundled-leanc contract of ``build_git_shim``."""

    def test_returns_none_on_non_windows(self, tmp_path: Path) -> None:
        from download import build_git_shim

        assert build_git_shim(tmp_path, "linux-x64") is None
        assert build_git_shim(tmp_path, "darwin-arm64") is None

    def test_windows_requires_lean_toolchain(self, tmp_path: Path) -> None:
        from download import build_git_shim

        with pytest.raises(RuntimeError, match="requires the Lean toolchain"):
            build_git_shim(tmp_path, "windows")

    def test_windows_requires_bundled_leanc(self, tmp_path: Path) -> None:
        from download import build_git_shim

        lean_dir = tmp_path / "lean"
        lean_dir.mkdir()
        with pytest.raises(RuntimeError, match=r"missing .*leanc\.exe"):
            build_git_shim(tmp_path, "windows", lean_dir=lean_dir)

    def test_builds_with_bundled_leanc(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import download

        lean_dir = tmp_path / "lean"
        leanc = lean_dir / "bin" / "leanc.exe"
        leanc.parent.mkdir(parents=True)
        leanc.touch()
        calls = []

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            output = Path(cmd[cmd.index("-o") + 1])
            image = bytearray(0x84)
            image[:2] = b"MZ"
            image[0x3C:0x40] = (0x80).to_bytes(4, "little")
            image[0x80:0x84] = b"PE\x00\x00"
            output.write_bytes(image)
            return Result()

        monkeypatch.setattr(download.subprocess, "run", fake_run)

        output = download.build_git_shim(
            tmp_path, "windows", lean_dir=lean_dir,
        )

        assert output == tmp_path / "git-shim" / "git.exe"
        assert calls == [
            ([
                str(leanc), "-O2", "-Wall", "-Wextra", "-s",
                "-o", str(output), str(SHIM_SOURCE),
            ], {"capture_output": True, "text": True})
        ]
