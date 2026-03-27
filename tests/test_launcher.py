"""Test the actual launcher scripts (start_lean.cmd / start_lean.sh).

Runs the real launcher template with the VSCodium invocation replaced by an
environment probe, then verifies the environment is set correctly.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def _create_fake_bundle(root: Path) -> None:
    """Create the minimal directory structure the launcher scripts expect."""
    (root / "lean" / "bin").mkdir(parents=True)
    (root / "lean" / "lib" / "lean").mkdir(parents=True)
    (root / "git" / "cmd").mkdir(parents=True)
    (root / "project" / ".lake" / "build" / "lib" / "lean").mkdir(parents=True)
    # Two packages WITH build dirs (should appear in LEAN_PATH)
    for pkg in ("mathlib", "aesop"):
        (root / "project" / ".lake" / "packages" / pkg
         / ".lake" / "build" / "lib" / "lean").mkdir(parents=True)
    # One package WITHOUT a build dir (should NOT appear in LEAN_PATH)
    (root / "project" / ".lake" / "packages" / "batteries").mkdir(parents=True)
    # Dummy VSCodium entries so the script doesn't complain
    (root / "vscodium").mkdir(parents=True)


def _parse_probe_output(path: Path) -> dict[str, str]:
    """Parse the bracketed-header output written by the probe."""
    result: dict[str, str] = {}
    current_key = None
    for line in path.read_text().splitlines():
        m = re.match(r"^\[(.+)\]$", line)
        if m:
            current_key = m.group(1)
        elif current_key is not None:
            result[current_key] = line
            current_key = None
    return result


# ---------------------------------------------------------------------------
# Unix launcher tests
# ---------------------------------------------------------------------------

UNIX_PROBE = """\
env > "$BUNDLE_ROOT/_test_env.txt"
(
    echo "[LAUNCH_ARG]"
    echo "$BUNDLE_ROOT/project"
) > "$BUNDLE_ROOT/_test_probe.txt"
"""


def _patch_unix_launcher(template: Path, output: Path) -> None:
    """Replace the VSCodium launch block with an environment probe."""
    text = template.read_text()
    # Replace from "# Launch VSCodium\n" (exact line) through end of file.
    # This must NOT match "# Launch VSCodium with Lean 4..." on line 2.
    text = re.sub(
        r"^# Launch VSCodium\n.*",
        UNIX_PROBE,
        text,
        flags=re.MULTILINE | re.DOTALL,
    )
    output.write_text(text)
    os.chmod(output, 0o755)


@pytest.mark.skipif(sys.platform == "win32", reason="Unix only")
class TestLauncherUnix:
    @pytest.fixture()
    def result(self, tmp_path: Path) -> dict[str, str]:
        _create_fake_bundle(tmp_path)
        patched = tmp_path / "start_lean_test.sh"
        _patch_unix_launcher(TEMPLATES_DIR / "start_lean.sh", patched)
        bash = shutil.which("bash")
        assert bash, "bash not found"
        # Clear the variables we're testing so we can verify the script sets them
        clean_env = {
            k: v for k, v in os.environ.items()
            if k not in ("ELAN_HOME", "LEAN_PATH")
        }
        clean_env["HOME"] = str(tmp_path)
        subprocess.run(
            [bash, str(patched)],
            check=True,
            timeout=30,
            cwd=tmp_path,
            env=clean_env,
        )
        env_text = (tmp_path / "_test_env.txt").read_text()
        env = {}
        for line in env_text.splitlines():
            k, _, v = line.partition("=")
            if k:
                env[k] = v
        probe = _parse_probe_output(tmp_path / "_test_probe.txt")
        env.update(probe)
        env["_bundle_root"] = str(tmp_path)
        return env

    def test_path_contains_lean_bin(self, result: dict[str, str]) -> None:
        root = result["_bundle_root"]
        lean_bin = f"{root}/lean/bin"
        assert result["PATH"].startswith(lean_bin + ":"), (
            f"PATH should start with {lean_bin}:, got: {result['PATH'][:200]}"
        )

    def test_elan_home(self, result: dict[str, str]) -> None:
        root = result["_bundle_root"]
        assert result["ELAN_HOME"] == f"{root}/lean"

    def test_lean_path_contains_toolchain_lib(self, result: dict[str, str]) -> None:
        root = result["_bundle_root"]
        entries = result["LEAN_PATH"].split(":")
        assert f"{root}/lean/lib/lean" in entries

    def test_lean_path_contains_project_build(self, result: dict[str, str]) -> None:
        root = result["_bundle_root"]
        entries = result["LEAN_PATH"].split(":")
        assert f"{root}/project/.lake/build/lib/lean" in entries

    def test_lean_path_contains_package_build_dirs(self, result: dict[str, str]) -> None:
        entries = result["LEAN_PATH"].split(":")
        assert any("mathlib" in e for e in entries), f"mathlib not in LEAN_PATH: {entries}"
        assert any("aesop" in e for e in entries), f"aesop not in LEAN_PATH: {entries}"

    def test_lean_path_excludes_packages_without_build(self, result: dict[str, str]) -> None:
        entries = result["LEAN_PATH"].split(":")
        assert not any("batteries" in e for e in entries), (
            f"batteries should not be in LEAN_PATH: {entries}"
        )

    def test_workspace_argument(self, result: dict[str, str]) -> None:
        root = result["_bundle_root"]
        assert result["LAUNCH_ARG"] == f"{root}/project"


# ---------------------------------------------------------------------------
# Windows launcher tests
# ---------------------------------------------------------------------------

WINDOWS_PROBE = """\
(
    echo [PATH]
    echo !PATH!
    echo [ELAN_HOME]
    echo !ELAN_HOME!
    echo [LEAN_PATH]
    echo !LEAN_PATH!
    echo [LAUNCH_ARG]
    echo !BUNDLE_ROOT!\\project
) > "!BUNDLE_ROOT!\\_test_probe.txt"
"""


def _patch_windows_launcher(template: Path, output: Path) -> None:
    """Replace the VSCodium launch line with an environment probe."""
    text = template.read_text()
    # Replace the "start ..." line at the end.
    # Use a lambda to avoid re.sub interpreting backslashes in the
    # replacement string as group references (\p → bad escape).
    text = re.sub(
        r"^:: Launch VSCodium.*\nstart .*$",
        lambda _: WINDOWS_PROBE,
        text,
        flags=re.MULTILINE,
    )
    output.write_text(text)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows only")
class TestLauncherWindows:
    @pytest.fixture()
    def result(self, tmp_path: Path) -> dict[str, str]:
        _create_fake_bundle(tmp_path)
        patched = tmp_path / "start_lean_test.cmd"
        _patch_windows_launcher(TEMPLATES_DIR / "start_lean.cmd", patched)
        clean_env = {
            k: v for k, v in os.environ.items()
            if k.upper() not in ("ELAN_HOME", "LEAN_PATH")
        }
        r = subprocess.run(
            ["cmd", "/c", str(patched)],
            check=False,
            timeout=30,
            cwd=tmp_path,
            capture_output=True,
            text=True,
            env=clean_env,
        )
        assert r.returncode == 0, (
            f"Launcher exited with {r.returncode}\n"
            f"stdout: {r.stdout[:2000]}\n"
            f"stderr: {r.stderr[:2000]}\n"
            f"script: {patched.read_text()[:2000]}"
        )
        return _parse_probe_output(tmp_path / "_test_probe.txt")

    def test_path_contains_lean_bin(self, result: dict[str, str]) -> None:
        path_val = result["PATH"]
        assert "\\lean\\bin" in path_val

    def test_path_contains_git_cmd(self, result: dict[str, str]) -> None:
        path_val = result["PATH"]
        assert "\\git\\cmd" in path_val

    def test_elan_home(self, result: dict[str, str]) -> None:
        assert result["ELAN_HOME"].endswith("\\lean")

    def test_lean_path_contains_toolchain_lib(self, result: dict[str, str]) -> None:
        entries = result["LEAN_PATH"].split(";")
        assert any(e.endswith("\\lean\\lib\\lean") for e in entries)

    def test_lean_path_contains_project_build(self, result: dict[str, str]) -> None:
        entries = result["LEAN_PATH"].split(";")
        assert any("project\\.lake\\build\\lib\\lean" in e for e in entries)

    def test_lean_path_contains_package_build_dirs(self, result: dict[str, str]) -> None:
        entries = result["LEAN_PATH"].split(";")
        assert any("mathlib" in e for e in entries), f"mathlib not in LEAN_PATH: {entries}"
        assert any("aesop" in e for e in entries), f"aesop not in LEAN_PATH: {entries}"

    def test_lean_path_excludes_packages_without_build(self, result: dict[str, str]) -> None:
        entries = result["LEAN_PATH"].split(";")
        assert not any("batteries" in e for e in entries), (
            f"batteries should not be in LEAN_PATH: {entries}"
        )

    def test_workspace_argument(self, result: dict[str, str]) -> None:
        assert result["LAUNCH_ARG"].endswith("\\project")


# ---------------------------------------------------------------------------
# Integration test: run the actual launcher from an extracted bundle
# ---------------------------------------------------------------------------

BUNDLE_ROOT = os.environ.get("BUNDLE_ROOT")


def _find_bundle_launcher(bundle_root: str, ext: str) -> Path | None:
    """Find the launcher script in the bundle root."""
    root = Path(bundle_root)
    for name in (f"Start Lean{ext}", f"start_lean{ext}"):
        p = root / name
        if p.exists():
            return p
    return None


@pytest.mark.skipif(
    not BUNDLE_ROOT or sys.platform == "win32",
    reason="Requires BUNDLE_ROOT env var on Unix",
)
class TestBundleLauncherUnix:
    """Test the actual launcher installed in an extracted bundle (Linux/macOS)."""

    @pytest.fixture()
    def result(self) -> dict[str, str]:
        bundle_root = Path(BUNDLE_ROOT)
        launcher = _find_bundle_launcher(BUNDLE_ROOT, ".sh")
        assert launcher, f"No .sh launcher found in {BUNDLE_ROOT}"
        patched = bundle_root / "_test_launcher.sh"
        try:
            _patch_unix_launcher(launcher, patched)
            clean_env = {
                k: v for k, v in os.environ.items()
                if k not in ("ELAN_HOME", "LEAN_PATH")
            }
            clean_env["HOME"] = str(bundle_root)
            bash = shutil.which("bash")
            assert bash, "bash not found"
            r = subprocess.run(
                [bash, str(patched)],
                check=False,
                timeout=30,
                cwd=str(bundle_root),
                capture_output=True,
                text=True,
                env=clean_env,
            )
            assert r.returncode == 0, (
                f"Bundle launcher exited with {r.returncode}\n"
                f"stdout: {r.stdout[:2000]}\n"
                f"stderr: {r.stderr[:2000]}"
            )
            env_text = (bundle_root / "_test_env.txt").read_text()
            env = {}
            for line in env_text.splitlines():
                k, _, v = line.partition("=")
                if k:
                    env[k] = v
            probe = _parse_probe_output(bundle_root / "_test_probe.txt")
            env.update(probe)
            return env
        finally:
            patched.unlink(missing_ok=True)
            (bundle_root / "_test_env.txt").unlink(missing_ok=True)
            (bundle_root / "_test_probe.txt").unlink(missing_ok=True)

    def test_path_contains_lean_bin(self, result: dict[str, str]) -> None:
        assert "/lean/bin" in result["PATH"]

    def test_elan_home_exists(self, result: dict[str, str]) -> None:
        assert Path(result["ELAN_HOME"]).is_dir()

    def test_lean_path_dirs_exist(self, result: dict[str, str]) -> None:
        for entry in result["LEAN_PATH"].split(":"):
            entry = entry.strip()
            if entry:
                assert Path(entry).is_dir(), f"LEAN_PATH entry does not exist: {entry}"

    def test_workspace_argument(self, result: dict[str, str]) -> None:
        assert Path(result["LAUNCH_ARG"]).is_dir()


@pytest.mark.skipif(
    not BUNDLE_ROOT or sys.platform != "win32",
    reason="Requires BUNDLE_ROOT env var on Windows",
)
class TestBundleLauncherWindows:
    """Test the actual launcher installed in an extracted bundle."""

    @pytest.fixture()
    def result(self) -> dict[str, str]:
        bundle_root = Path(BUNDLE_ROOT)
        launcher = _find_bundle_launcher(BUNDLE_ROOT, ".cmd")
        assert launcher, f"No .cmd launcher found in {BUNDLE_ROOT}"
        # Place the patched script in the bundle root so %~dp0 resolves
        # to the correct BUNDLE_ROOT (the script derives its root from
        # its own location).
        patched = bundle_root / "_test_launcher.cmd"
        try:
            _patch_windows_launcher(launcher, patched)
            clean_env = {
                k: v for k, v in os.environ.items()
                if k.upper() not in ("ELAN_HOME", "LEAN_PATH")
            }
            r = subprocess.run(
                ["cmd", "/c", str(patched)],
                check=False,
                timeout=30,
                cwd=str(bundle_root),
                capture_output=True,
                text=True,
                env=clean_env,
            )
            assert r.returncode == 0, (
                f"Bundle launcher exited with {r.returncode}\n"
                f"stdout: {r.stdout[:2000]}\n"
                f"stderr: {r.stderr[:2000]}"
            )
            return _parse_probe_output(bundle_root / "_test_probe.txt")
        finally:
            patched.unlink(missing_ok=True)
            (bundle_root / "_test_probe.txt").unlink(missing_ok=True)

    def test_path_contains_lean_bin(self, result: dict[str, str]) -> None:
        assert "\\lean\\bin" in result["PATH"]

    def test_elan_home_exists(self, result: dict[str, str]) -> None:
        assert Path(result["ELAN_HOME"]).is_dir()

    def test_lean_path_dirs_exist(self, result: dict[str, str]) -> None:
        for entry in result["LEAN_PATH"].split(";"):
            entry = entry.strip()
            if entry:
                assert Path(entry).is_dir(), f"LEAN_PATH entry does not exist: {entry}"

    def test_workspace_argument(self, result: dict[str, str]) -> None:
        assert Path(result["LAUNCH_ARG"]).is_dir()
