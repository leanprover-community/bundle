import os
import subprocess
import sys
import zipfile
from pathlib import Path

import json
import pytest

import bundle

from bundle import (
    _WORK_DIR_MARKER,
    _detect_host_platform,
    _prepare_work_dir,
    build_project,
    clone_project,
)
from download import trim_lean_toolchain

from assemble import (
    _BUNDLE_CRITICAL_SETTINGS,
    _BUNDLE_USER_SETTINGS,
    _WATERPROOF_CRITICAL_SETTINGS,
    _WATERPROOF_USER_SETTINGS,
    _module_stem_from_build_path,
    _parse_jsonc,
    _patch_workspace_settings,
    _reset_bundle_dir,
    _rewrite_lakefile_lean_deps,
    _rewrite_lakefile_toml_deps,
    copy_lake_selective,
    install_lake_wrapper,
    prune_ir_from_bundle,
    setup_vscodium_portable,
)


def test_no_zip_without_work_dir_is_rejected() -> None:
    """--no-zip without --work-dir must be rejected before any work starts (#27)."""
    result = subprocess.run(
        [sys.executable, "bundle.py", "https://example.invalid/repo", "--no-zip"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--no-zip requires --work-dir" in result.stderr


def test_clean_work_dir_without_work_dir_is_rejected() -> None:
    result = subprocess.run(
        [
            sys.executable,
            "bundle.py",
            "https://example.invalid/repo",
            "--clean-work-dir",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "--clean-work-dir requires --work-dir" in result.stderr


def test_cross_platform_bundle_is_rejected_before_work_starts() -> None:
    target = "linux-x64" if sys.platform == "win32" else "windows"
    result = subprocess.run(
        [
            sys.executable, "bundle.py", "https://example.invalid/repo",
            "--platform", target,
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "Cross-platform bundles are not supported" in result.stderr
    assert f"requested {target}" in result.stderr


@pytest.mark.parametrize(
    "waterproof_args",
    [
        ["--waterproof"],
        ["--waterproof-version", "0.12.0"],
        ["--waterproof-vsix", "waterproof.vsix"],
    ],
)
def test_open_file_is_rejected_for_windows_waterproof_before_work_starts(
    waterproof_args: list[str],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(bundle, "_detect_host_platform", lambda: "windows")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bundle.py",
            "https://example.invalid/repo",
            "--platform",
            "windows",
            *waterproof_args,
            "--open-file",
            "Course/Sheet.lean",
        ],
    )

    with pytest.raises(SystemExit) as exc_info:
        bundle.main()

    assert exc_info.value.code == 2
    assert (
        "--open-file is not supported for Waterproof bundles on Windows"
        in capsys.readouterr().err
    )


def test_open_file_remains_supported_for_regular_windows_bundles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class CloneReached(Exception):
        pass

    def fake_clone(*args, **kwargs):
        raise CloneReached

    monkeypatch.setattr(bundle, "_detect_host_platform", lambda: "windows")
    monkeypatch.setattr(bundle, "clone_project", fake_clone)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "bundle.py",
            "https://example.invalid/repo",
            "--platform",
            "windows",
            "--open-file",
            "Course/Sheet.lean",
            "--work-dir",
            str(tmp_path / "work"),
        ],
    )

    with pytest.raises(CloneReached):
        bundle.main()


@pytest.mark.parametrize(
    ("system", "machine", "expected"),
    [
        ("Windows", "AMD64", "windows"),
        ("Linux", "x86_64", "linux-x64"),
        ("Linux", "aarch64", "linux-arm64"),
        ("Darwin", "x86_64", "darwin-x64"),
        ("Darwin", "arm64", "darwin-arm64"),
    ],
)
def test_detect_host_platform_accepts_supported_architectures(
    monkeypatch: pytest.MonkeyPatch,
    system: str,
    machine: str,
    expected: str,
) -> None:
    monkeypatch.setattr("bundle.platform.system", lambda: system)
    monkeypatch.setattr("bundle.platform.machine", lambda: machine)

    assert _detect_host_platform() == expected


@pytest.mark.parametrize("machine", ["i686", "ppc64le", "riscv64", "s390x"])
def test_detect_host_platform_rejects_unsupported_linux_architectures(
    monkeypatch: pytest.MonkeyPatch,
    machine: str,
) -> None:
    monkeypatch.setattr("bundle.platform.system", lambda: "Linux")
    monkeypatch.setattr("bundle.platform.machine", lambda: machine)

    with pytest.raises(RuntimeError, match=f"linux/{machine}"):
        _detect_host_platform()


def test_clone_project_removes_previous_clone(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "project"
    stale_file = destination / "stale.txt"
    stale_file.parent.mkdir()
    stale_file.write_text("left by an interrupted run")

    def fake_run(cmd, **kwargs):
        assert cmd == [
            "git",
            "clone",
            "--depth=1",
            "https://example.invalid/repo",
            str(destination),
        ]
        assert not destination.exists()

    monkeypatch.setattr("bundle.subprocess.run", fake_run)

    assert clone_project("https://example.invalid/repo", destination) == destination


def test_clone_project_checks_out_a_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    destination = tmp_path / "project"
    commit = "e62b9166113d3f48b82a09bd5e728fbd779608cc"
    calls = []
    monkeypatch.setattr(
        "bundle.subprocess.run",
        lambda cmd, **kwargs: calls.append(cmd),
    )

    clone_project("https://example.invalid/repo", destination, ref=commit)

    assert calls == [
        [
            "git", "clone", "--depth=1", "--no-checkout",
            "https://example.invalid/repo", str(destination),
        ],
        ["git", "-C", str(destination), "fetch", "--depth=1", "origin", commit],
        ["git", "-C", str(destination), "checkout", "--detach", "FETCH_HEAD"],
    ]


def test_prepare_work_dir_preserves_existing_contents_by_default(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "existing-work"
    sentinel = work_dir / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("keep")

    prepared = _prepare_work_dir(work_dir)

    assert prepared == work_dir.resolve()
    assert sentinel.read_text() == "keep"
    assert not (prepared / _WORK_DIR_MARKER).exists()


def test_clean_work_dir_removes_all_previous_contents(tmp_path: Path) -> None:
    work_dir = _prepare_work_dir(tmp_path / "dedicated-work")
    marker = work_dir / _WORK_DIR_MARKER
    assert marker.is_file()

    stale_vscodium = work_dir / "downloads" / "vscodium" / "obsolete.exe"
    stale_extension = (
        work_dir / "downloads" / "waterproof-tue.waterproof-0.12.0"
        / "obsolete.js"
    )
    stale_vscodium.parent.mkdir(parents=True)
    stale_extension.parent.mkdir(parents=True)
    stale_vscodium.write_bytes(b"old")
    stale_extension.write_bytes(b"old")

    prepared = _prepare_work_dir(work_dir, clean=True)

    assert prepared == work_dir.resolve()
    assert marker.is_file()
    assert [path.name for path in prepared.iterdir()] == [_WORK_DIR_MARKER]


def test_clean_work_dir_rejects_missing_marker(tmp_path: Path) -> None:
    work_dir = tmp_path / "unowned"
    sentinel = work_dir / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("caller-owned")

    with pytest.raises(ValueError, match="unowned work directory"):
        _prepare_work_dir(work_dir, clean=True)

    assert sentinel.read_text() == "caller-owned"


def test_clean_work_dir_rejects_an_input_inside_it(tmp_path: Path) -> None:
    work_dir = _prepare_work_dir(tmp_path / "work")
    project_dir = work_dir / "project"
    sentinel = project_dir / "keep.txt"
    sentinel.parent.mkdir()
    sentinel.write_text("caller-owned")

    with pytest.raises(ValueError, match="overlaps an input path"):
        _prepare_work_dir(
            work_dir,
            clean=True,
            protected_paths=(project_dir,),
        )

    assert sentinel.read_text() == "caller-owned"


def test_clean_work_dir_rejects_current_directory() -> None:
    with pytest.raises(ValueError, match="unsafe work directory"):
        _prepare_work_dir(Path.cwd(), clean=True)


def test_allow_unsolved_tolerates_failed_build(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    lake = str(Path("/toolchain/bin/lake"))

    class Result:
        def __init__(self, returncode: int):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        if command == [lake, "build"]:
            return Result(1)
        return Result(0)

    monkeypatch.setattr("bundle.subprocess.run", fake_run)

    build_project(
        tmp_path,
        Path(lake),
        "linux-x64",
        allow_unsolved=True,
    )

    assert calls == [
        [lake, "exe", "cache", "get"],
        [lake, "build"],
    ]


@pytest.mark.parametrize(
    ("retry_returncode", "warns"),
    [(0, False), (1, True)],
)
def test_allow_unsolved_retries_windows_build_serially_before_continuing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    retry_returncode: int,
    warns: bool,
) -> None:
    calls: list[list[str]] = []
    calls_with_env: list[dict[str, str]] = []
    build_attempt = 0
    lake = str(Path("/toolchain/bin/lake"))

    class Result:
        def __init__(self, returncode: int):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = ""

    def fake_run(command, **kwargs):
        nonlocal build_attempt
        calls.append(command)
        calls_with_env.append(kwargs.get("env", {}))
        if command == [lake, "build"]:
            build_attempt += 1
            return Result(1 if build_attempt == 1 else retry_returncode)
        return Result(0)

    monkeypatch.setenv("LEAN_NUM_THREADS", "8")
    monkeypatch.setattr("bundle.subprocess.run", fake_run)

    build_project(
        tmp_path,
        Path(lake),
        "windows",
        allow_unsolved=True,
    )

    assert calls == [
        [lake, "exe", "cache", "get"],
        [lake, "build"],
        [lake, "build"],
    ]
    assert calls_with_env[0]["LEAN_NUM_THREADS"] == "8"
    assert calls_with_env[1]["LEAN_NUM_THREADS"] == "8"
    assert calls_with_env[2]["LEAN_NUM_THREADS"] == "1"
    output = capsys.readouterr().out
    assert "retrying serially on Windows" in output
    assert ("--allow-unsolved was specified" in output) is warns


class TestTrimLeanToolchain:
    @staticmethod
    def _make_toolchain(root: Path) -> None:
        for rel in [
            "bin/lean.exe",
            "bin/lake.exe",
            "bin/clang.exe",
            "bin/llvm-ar.exe",
            "bin/ld.lld.exe",
            "include/lean/lean.h",
            "lib/clang/include/stddef.h",
            "lib/lean/libleanrt.a",
        ]:
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"tool")

    def test_removes_build_tools(self, tmp_path: Path):
        self._make_toolchain(tmp_path)

        trim_lean_toolchain(tmp_path, "windows")

        assert (tmp_path / "bin" / "lean.exe").is_file()
        assert (tmp_path / "bin" / "lake.exe").is_file()
        assert not (tmp_path / "bin" / "clang.exe").exists()
        assert not (tmp_path / "bin" / "llvm-ar.exe").exists()
        assert not (tmp_path / "include").exists()
        assert not (tmp_path / "lib" / "clang").exists()
        assert not (tmp_path / "lib" / "lean" / "libleanrt.a").exists()


class TestModuleStemFromBuildPath:
    def test_build_lib_lean_olean(self):
        parts = ("build", "lib", "lean", "Mathlib", "Algebra", "Group", "Basic.olean")
        assert _module_stem_from_build_path(parts) == "Mathlib/Algebra/Group/Basic"

    def test_build_lib_lean_multi_extension(self):
        parts = ("build", "lib", "lean", "Mathlib", "Foo.olean.private")
        assert _module_stem_from_build_path(parts) == "Mathlib/Foo"

    def test_build_lib_lean_ir_hash(self):
        parts = ("build", "lib", "lean", "Mathlib", "Foo.ir.hash")
        assert _module_stem_from_build_path(parts) == "Mathlib/Foo"

    def test_build_ir(self):
        parts = ("build", "ir", "Mathlib", "Foo.c")
        assert _module_stem_from_build_path(parts) == "Mathlib/Foo"

    def test_packages_nested(self):
        parts = ("packages", "mathlib", ".lake", "build", "lib", "lean", "Mathlib", "Foo.olean")
        assert _module_stem_from_build_path(parts) == "Mathlib/Foo"

    def test_no_build_marker(self):
        parts = ("packages", "mathlib", "Mathlib", "Foo.lean")
        assert _module_stem_from_build_path(parts) is None

    def test_config_file(self):
        parts = ("packages", "mathlib", "lakefile.lean")
        assert _module_stem_from_build_path(parts) is None

    def test_root_module(self):
        parts = ("build", "lib", "lean", "Mathlib.olean")
        assert _module_stem_from_build_path(parts) == "Mathlib"


class TestCopyLakeSelective:
    def _make_lake(self, tmp_path: Path) -> Path:
        """Create a fake .lake/ tree with build artifacts for two modules."""
        lake = tmp_path / "src" / ".lake"

        # Project build artifacts
        proj_build = lake / "build" / "lib" / "lean"
        (proj_build / "MyProject").mkdir(parents=True)
        (proj_build / "MyProject" / "Needed.olean").write_bytes(b"n1")
        (proj_build / "MyProject" / "Needed.ilean").write_bytes(b"n2")
        (proj_build / "MyProject" / "Unneeded.olean").write_bytes(b"u1")

        # Dep build artifacts
        pkg_build = lake / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
        (pkg_build / "Mathlib" / "Used").mkdir(parents=True)
        (pkg_build / "Mathlib" / "Used" / "Mod.olean").write_bytes(b"m1")
        (pkg_build / "Mathlib" / "Unused").mkdir(parents=True)
        (pkg_build / "Mathlib" / "Unused" / "Big.olean").write_bytes(b"b1")

        # Infrastructure (always copied)
        (lake / "packages" / "mathlib" / "lakefile.lean").write_text("lakefile")
        (lake / "packages" / "mathlib" / "lean-toolchain").write_text("v4.17.0")

        return lake

    def test_none_copies_everything(self, tmp_path: Path):
        lake = self._make_lake(tmp_path)
        dst = tmp_path / "dst" / ".lake"
        n_copied, n_skipped = copy_lake_selective(lake, dst, None)
        assert n_skipped == 0
        assert (dst / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib" / "Unused" / "Big.olean").exists()

    def test_selective_prunes_unneeded(self, tmp_path: Path):
        lake = self._make_lake(tmp_path)
        dst = tmp_path / "dst" / ".lake"
        needed = {"MyProject/Needed", "Mathlib/Used/Mod"}
        n_copied, n_skipped = copy_lake_selective(lake, dst, needed)

        # Needed artifacts present
        assert (dst / "build" / "lib" / "lean" / "MyProject" / "Needed.olean").exists()
        assert (dst / "build" / "lib" / "lean" / "MyProject" / "Needed.ilean").exists()
        assert (dst / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib" / "Used" / "Mod.olean").exists()

        # Unneeded artifacts pruned
        assert not (dst / "build" / "lib" / "lean" / "MyProject" / "Unneeded.olean").exists()
        assert not (dst / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib" / "Unused" / "Big.olean").exists()

        # Infrastructure always copied
        assert (dst / "packages" / "mathlib" / "lakefile.lean").exists()
        assert (dst / "packages" / "mathlib" / "lean-toolchain").exists()

        assert n_skipped > 0

    def test_directory_pruning(self, tmp_path: Path):
        """Entire directories are skipped when no needed stem has a matching prefix."""
        lake = self._make_lake(tmp_path)
        dst = tmp_path / "dst" / ".lake"
        # Only need project module; all Mathlib build dirs should be skipped
        needed = {"MyProject/Needed"}
        copy_lake_selective(lake, dst, needed)

        # The Mathlib build directory tree should not exist
        mathlib_build = dst / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib"
        assert not mathlib_build.exists()


class TestResetBundleDir:
    def test_removes_stale_files_and_symlinks(self, tmp_path: Path):
        bundle = tmp_path / "course-bundle"
        stale = bundle / "project" / ".lake" / "packages" / "mathlib"
        stale.mkdir(parents=True)
        (stale / "artifact.olean").write_bytes(b"stale")
        try:
            (stale / "run.py").symlink_to("run")
        except OSError as exc:
            if sys.platform == "win32" and exc.winerror == 1314:
                pytest.skip("Windows user cannot create symbolic links")
            raise

        _reset_bundle_dir(bundle)

        assert bundle.is_dir()
        assert list(bundle.iterdir()) == []

    def test_refuses_to_replace_a_symlink(self, tmp_path: Path):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        bundle = tmp_path / "course-bundle"
        try:
            bundle.symlink_to(real_dir, target_is_directory=True)
        except OSError as exc:
            if sys.platform == "win32" and exc.winerror == 1314:
                pytest.skip("Windows user cannot create symbolic links")
            raise

        with pytest.raises(ValueError, match="not a directory"):
            _reset_bundle_dir(bundle)

        assert real_dir.is_dir()



def test_prune_ir_from_bundle_removes_lean_ir_payloads(tmp_path: Path) -> None:
    bundle_project = tmp_path / "project"

    # Main project .lake tree with an .ir payload and its freshness sidecar.
    lib_lean = bundle_project / ".lake" / "build" / "lib" / "lean"
    lib_lean.mkdir(parents=True)
    (lib_lean / "Mdd154.olean").write_bytes(b"olean")
    (lib_lean / "Mdd154.ilean").write_bytes(b"ilean")
    (lib_lean / "Mdd154.olean.private").write_bytes(b"private")
    (lib_lean / "Mdd154.ir").write_bytes(b"x" * 1024)
    (lib_lean / "Mdd154.ir.hash").write_bytes(b"h1")
    (lib_lean / "Mdd154.trace").write_text("trace content")

    # A package with a Lean IR payload under build/lib/lean/ and a native
    # compilation intermediate directory under .lake/build/ir/.
    pkg_lib = bundle_project / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib"
    pkg_lib.mkdir(parents=True)
    (pkg_lib / "Bar.olean").write_bytes(b"olean")
    (pkg_lib / "Bar.ir").write_bytes(b"y" * 512)
    (pkg_lib / "Bar.ir.hash").write_bytes(b"h2")
    pkg_ir = bundle_project / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "ir"
    pkg_ir.mkdir(parents=True)
    (pkg_ir / "Bar.c").write_bytes(b"z" * 4096)
    (pkg_ir / "Bar.c.hash").write_bytes(b"hc")

    n, freed = prune_ir_from_bundle(bundle_project)

    # ``*.ir`` payloads are gone
    assert not (lib_lean / "Mdd154.ir").exists()
    assert not (pkg_lib / "Bar.ir").exists()

    # Everything else — including the old-style build/ir/ tree, the
    # .ir.hash sidecars, the olean facets, and the traces — is left alone
    # so Lake's freshness check still considers the target up-to-date.
    assert (lib_lean / "Mdd154.olean").exists()
    assert (lib_lean / "Mdd154.ilean").exists()
    assert (lib_lean / "Mdd154.olean.private").exists()
    assert (lib_lean / "Mdd154.ir.hash").read_bytes() == b"h1"
    assert (lib_lean / "Mdd154.trace").read_text() == "trace content"
    assert (pkg_lib / "Bar.olean").exists()
    assert (pkg_lib / "Bar.ir.hash").read_bytes() == b"h2"
    assert (pkg_ir / "Bar.c").read_bytes() == b"z" * 4096
    assert (pkg_ir / "Bar.c.hash").read_bytes() == b"hc"

    # Counters reflect only the ``*.ir`` deletions.
    assert n == 2
    assert freed == 1024 + 512


@pytest.mark.skipif(sys.platform == "win32", reason="Unix wrapper only")
def test_install_lake_wrapper_strips_ir(tmp_path: Path) -> None:
    bundle_dir = tmp_path / "bundle"
    lake_bin = bundle_dir / "lean" / "bin"
    lake_bin.mkdir(parents=True)
    (lake_bin / "lake").write_text("#!/bin/sh\necho real")
    (lake_bin / "lake").chmod(0o755)

    install_lake_wrapper(bundle_dir, "linux-x64")

    assert (lake_bin / "lake.real").exists()
    assert (lake_bin / "lake").exists()
    wrapper_text = (lake_bin / "lake").read_text()
    assert "setup-file" in wrapper_text
    assert "lake.real" in wrapper_text


@pytest.mark.skipif(sys.platform == "win32", reason="Unix wrapper only")
class TestLakeWrapperIntegration:
    """Integration tests that install the wrapper and run it end-to-end."""

    @staticmethod
    def _make_fake_lake(lake_bin: Path, output: str) -> None:
        """Create a fake lake.real that echoes *output* for ``setup-file``."""
        lake_bin.mkdir(parents=True)
        # The wrapper renames lake → lake.real, so we write lake first.
        script = "#!/bin/sh\n" + f'printf %s \'{output}\'\n'
        (lake_bin / "lake").write_text(script)
        (lake_bin / "lake").chmod(0o755)

    def test_strips_ir_entries(self, tmp_path: Path) -> None:
        import subprocess
        bundle_dir = tmp_path / "bundle"
        lake_bin = bundle_dir / "lean" / "bin"
        payload = json.dumps({"importArts": {"Foo": ["Foo.olean", "Foo.ir"]}})
        self._make_fake_lake(lake_bin, payload)
        install_lake_wrapper(bundle_dir, "linux-x64")

        result = subprocess.run(
            [str(lake_bin / "lake"), "setup-file", "Foo.lean"],
            capture_output=True, timeout=5,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert output["importArts"]["Foo"] == ["Foo.olean"]

    def test_fallback_passes_through_non_json(self, tmp_path: Path) -> None:
        import subprocess
        bundle_dir = tmp_path / "bundle"
        lake_bin = bundle_dir / "lean" / "bin"
        non_json = "warning: something\n{not json at all}\n"
        self._make_fake_lake(lake_bin, non_json)
        install_lake_wrapper(bundle_dir, "linux-x64")

        result = subprocess.run(
            [str(lake_bin / "lake"), "setup-file", "Foo.lean"],
            capture_output=True, timeout=5,
        )
        assert result.returncode == 0
        assert result.stdout.decode() == non_json

    def test_non_setup_file_passes_through(self, tmp_path: Path) -> None:
        import subprocess
        bundle_dir = tmp_path / "bundle"
        lake_bin = bundle_dir / "lean" / "bin"
        lake_bin.mkdir(parents=True)
        (lake_bin / "lake").write_text("#!/bin/sh\necho real-lake-output")
        (lake_bin / "lake").chmod(0o755)
        install_lake_wrapper(bundle_dir, "linux-x64")

        result = subprocess.run(
            [str(lake_bin / "lake"), "build"],
            capture_output=True, timeout=5,
        )
        assert result.returncode == 0
        assert b"real-lake-output" in result.stdout


def test_setup_vscodium_portable_uses_vsix_extension_subdir(tmp_path) -> None:
    vscodium_dir = tmp_path / "vscodium"
    vscodium_dir.mkdir()

    extension_dir = tmp_path / "leanprover.lean4-1.0.0"
    nested = extension_dir / "extension"
    nested.mkdir(parents=True)
    (nested / "package.json").write_text(json.dumps({"name": "lean4"}))

    settings_template = tmp_path / "settings.json"
    settings_template.write_text("{}")

    setup_vscodium_portable(
        vscodium_dir,
        [extension_dir],
        settings_template,
        user_settings_overrides=_BUNDLE_USER_SETTINGS,
    )

    ext_dest = vscodium_dir / "data" / "extensions" / extension_dir.name
    assert (ext_dest / "package.json").is_file()
    assert not (ext_dest / "extension" / "package.json").exists()
    user_settings = json.loads(
        (vscodium_dir / "data/user-data/User/settings.json").read_text()
    )
    assert user_settings["extensions.autoUpdate"] is False


def test_setup_vscodium_portable_makes_waterproof_editor_default(tmp_path) -> None:
    vscodium_dir = tmp_path / "vscodium"
    vscodium_dir.mkdir()

    extension_dir = tmp_path / "waterproof-tue.waterproof-local"
    nested = extension_dir / "extension"
    nested.mkdir(parents=True)
    package = {
        "publisher": "waterproof-tue",
        "name": "waterproof",
        "version": "0.12.0-dev",
        "contributes": {
            "customEditors": [{
                "viewType": "waterproofTue.waterproofEditor",
                "selector": [{"filenamePattern": "*.lean"}],
            }],
        },
    }
    (nested / "package.json").write_text(json.dumps(package))
    settings_template = tmp_path / "settings.json"
    settings_template.write_text("{}")

    setup_vscodium_portable(
        vscodium_dir,
        [extension_dir],
        settings_template,
        user_settings_overrides={
            **_BUNDLE_USER_SETTINGS,
            **_WATERPROOF_USER_SETTINGS,
        },
    )

    installed_package = json.loads((
        vscodium_dir / "data/extensions" / extension_dir.name / "package.json"
    ).read_text())
    editor = installed_package["contributes"]["customEditors"][0]
    assert editor["priority"] == "default"
    assert "priority" not in json.loads((nested / "package.json").read_text())[
        "contributes"
    ]["customEditors"][0]
    user_settings = json.loads((
        vscodium_dir / "data/user-data/User/settings.json"
    ).read_text())
    assert "workbench.editorAssociations" not in user_settings
    assert user_settings["window.autoDetectColorScheme"] is True
    assert user_settings["workbench.colorTheme"] == "waterproof-light"
    assert (
        user_settings["workbench.preferredLightColorTheme"]
        == "waterproof-light"
    )
    assert (
        user_settings["workbench.preferredDarkColorTheme"]
        == "waterproof-dark"
    )


# ---------------------------------------------------------------------------
# Workspace settings patching (issue #35)
# ---------------------------------------------------------------------------

class TestPatchWorkspaceSettings:
    """Test _patch_workspace_settings overrides bundle-critical settings."""

    def test_overrides_existing_settings(self, tmp_path: Path) -> None:
        """Project settings that conflict with bundle-critical values are overridden."""
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        (vscode_dir / "settings.json").write_text(json.dumps({
            "lean4.automaticallyBuildDependencies": True,
            "editor.fontSize": 14,
        }))

        _patch_workspace_settings(tmp_path, _BUNDLE_CRITICAL_SETTINGS)

        result = json.loads((vscode_dir / "settings.json").read_text())
        assert result["lean4.automaticallyBuildDependencies"] is False
        # Non-critical project settings are preserved
        assert result["editor.fontSize"] == 14

    def test_creates_settings_when_missing(self, tmp_path: Path) -> None:
        """settings.json is created if neither .vscode/ nor the file exist."""
        _patch_workspace_settings(tmp_path, _BUNDLE_CRITICAL_SETTINGS)

        settings_path = tmp_path / ".vscode" / "settings.json"
        assert settings_path.is_file()
        result = json.loads(settings_path.read_text())
        assert result["lean4.automaticallyBuildDependencies"] is False
        assert result["lean4.showSetupWarnings"] is False

    def test_preserves_all_project_settings(self, tmp_path: Path) -> None:
        """All non-conflicting project settings survive the patch."""
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        project_settings = {
            "editor.tabSize": 4,
            "editor.rulers": [80, 100],
            "lean4.input.leader": "\\",
        }
        (vscode_dir / "settings.json").write_text(json.dumps(project_settings))

        _patch_workspace_settings(tmp_path, {"lean4.automaticallyBuildDependencies": False})

        result = json.loads((vscode_dir / "settings.json").read_text())
        assert result["editor.tabSize"] == 4
        assert result["editor.rulers"] == [80, 100]
        assert result["lean4.input.leader"] == "\\"
        assert result["lean4.automaticallyBuildDependencies"] is False

    def test_waterproof_association_preserves_other_editor_associations(
        self, tmp_path: Path
    ) -> None:
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        (vscode_dir / "settings.json").write_text(json.dumps({
            "workbench.editorAssociations": {
                "*.md": "vscode.markdown.preview.editor",
                "*.lean": "project.otherEditor",
            },
        }))

        _patch_workspace_settings(tmp_path, _WATERPROOF_CRITICAL_SETTINGS)

        result = json.loads((vscode_dir / "settings.json").read_text())
        associations = result["workbench.editorAssociations"]
        assert associations == {
            "*.md": "vscode.markdown.preview.editor",
            "*.lean": "waterproofTue.waterproofEditor",
        }

    def test_waterproof_theme_settings_are_removed_from_workspace(
        self, tmp_path: Path
    ) -> None:
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        project_settings = {
            **_WATERPROOF_USER_SETTINGS,
            "editor.fontSize": 14,
        }
        (vscode_dir / "settings.json").write_text(json.dumps(project_settings))

        _patch_workspace_settings(
            tmp_path,
            _WATERPROOF_CRITICAL_SETTINGS,
            remove_settings=tuple(_WATERPROOF_USER_SETTINGS),
        )

        result = json.loads((vscode_dir / "settings.json").read_text())
        for key in _WATERPROOF_USER_SETTINGS:
            assert key not in result
        assert result["editor.fontSize"] == 14

    def test_creates_vscode_dir_when_missing(self, tmp_path: Path) -> None:
        """.vscode/ directory is created if it doesn't exist."""
        assert not (tmp_path / ".vscode").exists()
        _patch_workspace_settings(tmp_path, {"update.mode": "none"})
        assert (tmp_path / ".vscode").is_dir()

    def test_handles_jsonc_with_comments(self, tmp_path: Path) -> None:
        """Project settings.json with JSONC comments and trailing commas."""
        vscode_dir = tmp_path / ".vscode"
        vscode_dir.mkdir()
        (vscode_dir / "settings.json").write_text(
            '{\n'
            '  // project preference\n'
            '  "lean4.automaticallyBuildDependencies": true,\n'
            '  /* block comment */\n'
            '  "editor.fontSize": 14,\n'  # trailing comma
            '}\n'
        )

        _patch_workspace_settings(tmp_path, _BUNDLE_CRITICAL_SETTINGS)

        result = json.loads((vscode_dir / "settings.json").read_text())
        assert result["lean4.automaticallyBuildDependencies"] is False
        assert result["editor.fontSize"] == 14

    def test_all_critical_settings_present(self) -> None:
        """Smoke test: the constant has the expected keys."""
        assert "lean4.automaticallyBuildDependencies" in _BUNDLE_CRITICAL_SETTINGS
        assert "lean4.alwaysAskBeforeInstallingLeanVersions" in _BUNDLE_CRITICAL_SETTINGS
        assert "lean4.showSetupWarnings" in _BUNDLE_CRITICAL_SETTINGS
        assert "security.workspace.trust.enabled" in _BUNDLE_CRITICAL_SETTINGS
        assert (
            _BUNDLE_CRITICAL_SETTINGS[
                "workbench.secondarySideBar.defaultVisibility"
            ]
            == "hidden"
        )
        assert _WATERPROOF_CRITICAL_SETTINGS["waterproof.skipLaunchChecks"] == "lean4"
        assert _WATERPROOF_CRITICAL_SETTINGS["workbench.editorAssociations"] == {
            "*.lean": "waterproofTue.waterproofEditor",
        }
        assert _WATERPROOF_USER_SETTINGS == {
            "window.autoDetectColorScheme": True,
            "workbench.colorTheme": "waterproof-light",
            "workbench.preferredLightColorTheme": "waterproof-light",
            "workbench.preferredDarkColorTheme": "waterproof-dark",
        }


class TestParseJsonc:
    """Test _parse_jsonc handles VS Code-style JSONC."""

    def test_line_comments(self) -> None:
        assert _parse_jsonc('{\n// comment\n"a": 1\n}') == {"a": 1}

    def test_block_comments(self) -> None:
        assert _parse_jsonc('{"a": /* inline */ 1}') == {"a": 1}

    def test_trailing_comma_object(self) -> None:
        assert _parse_jsonc('{"a": 1, "b": 2,}') == {"a": 1, "b": 2}

    def test_trailing_comma_array(self) -> None:
        assert _parse_jsonc('{"a": [1, 2,]}') == {"a": [1, 2]}

    def test_strict_json_passthrough(self) -> None:
        assert _parse_jsonc('{"a": 1}') == {"a": 1}


# ---------------------------------------------------------------------------
# Lakefile.lean rewriting: bare, quoted, and guillemet require syntax
# ---------------------------------------------------------------------------

class TestRewriteLakefileLeanDeps:
    """Test _rewrite_lakefile_lean_deps handles all require name forms."""

    def test_bare_require(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        lakefile.write_text(
            'require mathlib from git "https://github.com/leanprover-community/mathlib4" @ "v1.0"\n',
            encoding="utf-8",
        )
        _rewrite_lakefile_lean_deps(tmp_path)
        assert lakefile.read_text(encoding="utf-8") == 'require mathlib from ".lake/packages/mathlib"\n'

    def test_quoted_require(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        lakefile.write_text(
            'require "mathlib" from git "https://github.com/leanprover-community/mathlib4" @ "v1.0"\n',
            encoding="utf-8",
        )
        _rewrite_lakefile_lean_deps(tmp_path)
        assert lakefile.read_text(encoding="utf-8") == 'require "mathlib" from ".lake/packages/mathlib"\n'

    def test_guillemet_require(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        lakefile.write_text(
            'require «doc-gen4» from git "https://github.com/leanprover/doc-gen4" @ "main"\n',
            encoding="utf-8",
        )
        _rewrite_lakefile_lean_deps(tmp_path)
        assert lakefile.read_text(encoding="utf-8") == 'require «doc-gen4» from ".lake/packages/doc-gen4"\n'

    def test_no_rev(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        lakefile.write_text(
            'require mathlib from git "https://github.com/leanprover-community/mathlib4"\n',
            encoding="utf-8",
        )
        _rewrite_lakefile_lean_deps(tmp_path)
        assert lakefile.read_text(encoding="utf-8") == 'require mathlib from ".lake/packages/mathlib"\n'

    def test_multiple_deps(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        lakefile.write_text(
            'require "mathlib" from git "https://github.com/leanprover-community/mathlib4" @ "v1.0"\n'
            'require «doc-gen4» from git "https://github.com/leanprover/doc-gen4" @ "main"\n'
            'require aesop from git "https://github.com/leanprover-community/aesop"\n',
            encoding="utf-8",
        )
        _rewrite_lakefile_lean_deps(tmp_path)
        expected = (
            'require "mathlib" from ".lake/packages/mathlib"\n'
            'require «doc-gen4» from ".lake/packages/doc-gen4"\n'
            'require aesop from ".lake/packages/aesop"\n'
        )
        assert lakefile.read_text(encoding="utf-8") == expected

    def test_commented_out_require_not_rewritten(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        original = '-- require mathlib from git "https://github.com/leanprover-community/mathlib4" @ "v1.0"\n'
        lakefile.write_text(original, encoding="utf-8")
        _rewrite_lakefile_lean_deps(tmp_path)
        assert lakefile.read_text(encoding="utf-8") == original

    def test_no_lakefile(self, tmp_path: Path) -> None:
        """No crash when lakefile.lean doesn't exist."""
        _rewrite_lakefile_lean_deps(tmp_path)


# ---------------------------------------------------------------------------
# Lakefile TOML rewriting (issue #23)
# ---------------------------------------------------------------------------


class TestRewriteLakefileTomlDeps:
    """Test _rewrite_lakefile_toml_deps handles all dep forms."""

    def test_standard_git_rev(self, tmp_path: Path) -> None:
        """Standard case: name immediately followed by git + rev."""
        (tmp_path / "lakefile.toml").write_text(
            '[[require]]\nname = "mathlib"\n'
            'git = "https://github.com/leanprover-community/mathlib4"\n'
            'rev = "main"\n'
        )
        _rewrite_lakefile_toml_deps(tmp_path)
        result = (tmp_path / "lakefile.toml").read_text()
        assert 'path = ".lake/packages/mathlib"' in result
        assert "git" not in result
        assert "rev" not in result

    def test_scope_branch_no_git(self, tmp_path: Path) -> None:
        """Pattern 1: scope + branch with no explicit git key."""
        (tmp_path / "lakefile.toml").write_text(
            '[[require]]\nname = "mathlib"\n'
            'scope = "leanprover-community"\n'
            'branch = "master"\n'
        )
        _rewrite_lakefile_toml_deps(tmp_path)
        result = (tmp_path / "lakefile.toml").read_text()
        assert 'path = ".lake/packages/mathlib"' in result
        assert "scope" not in result
        assert "branch" not in result

    def test_git_not_immediately_after_name(self, tmp_path: Path) -> None:
        """Pattern 2: scope between name and git."""
        (tmp_path / "lakefile.toml").write_text(
            '[[require]]\nname = "mathlib"\n'
            'scope = "leanprover-community"\n'
            'git = "https://github.com/leanprover-community/mathlib4"\n'
            'rev = "main"\n'
        )
        _rewrite_lakefile_toml_deps(tmp_path)
        result = (tmp_path / "lakefile.toml").read_text()
        assert 'path = ".lake/packages/mathlib"' in result
        assert "git" not in result
        assert "scope" not in result
        assert "rev" not in result

    def test_git_before_name(self, tmp_path: Path) -> None:
        """Pattern 3: git key appears before name."""
        (tmp_path / "lakefile.toml").write_text(
            "[[require]]\n"
            'git = "https://github.com/leanprover-community/mathlib4"\n'
            'name = "mathlib"\n'
        )
        _rewrite_lakefile_toml_deps(tmp_path)
        result = (tmp_path / "lakefile.toml").read_text()
        assert 'path = ".lake/packages/mathlib"' in result
        assert "git" not in result

    def test_multiple_deps(self, tmp_path: Path) -> None:
        """Multiple deps: one git, one scope+branch, one path (untouched)."""
        (tmp_path / "lakefile.toml").write_text(
            '[[require]]\nname = "mathlib"\n'
            'git = "https://github.com/leanprover-community/mathlib4"\n'
            'rev = "v4.0.0"\n'
            "\n"
            '[[require]]\nname = "aesop"\n'
            'scope = "leanprover-community"\n'
            'branch = "master"\n'
            "\n"
            '[[require]]\nname = "local_dep"\n'
            'path = "./my_dep"\n'
        )
        _rewrite_lakefile_toml_deps(tmp_path)
        result = (tmp_path / "lakefile.toml").read_text()
        assert 'path = ".lake/packages/mathlib"' in result
        assert 'path = ".lake/packages/aesop"' in result
        # The local path dep should be unchanged
        assert 'path = "./my_dep"' in result

    def test_no_lakefile(self, tmp_path: Path) -> None:
        """No lakefile.toml present — should be a no-op."""
        _rewrite_lakefile_toml_deps(tmp_path)  # should not raise

    def test_no_require_section(self, tmp_path: Path) -> None:
        """Lakefile with no [[require]] — should be a no-op."""
        (tmp_path / "lakefile.toml").write_text('[package]\nname = "foo"\n')
        _rewrite_lakefile_toml_deps(tmp_path)
        result = (tmp_path / "lakefile.toml").read_text()
        assert result == '[package]\nname = "foo"\n'

    def test_comment_before_name(self, tmp_path: Path) -> None:
        """A commented-out name line should not confuse the name extraction."""
        (tmp_path / "lakefile.toml").write_text(
            "[[require]]\n"
            '# name = "old"\n'
            'name = "mathlib"\n'
            'git = "https://github.com/leanprover-community/mathlib4"\n'
            'rev = "main"\n'
        )
        _rewrite_lakefile_toml_deps(tmp_path)
        result = (tmp_path / "lakefile.toml").read_text()
        assert 'path = ".lake/packages/mathlib"' in result
        assert '# name = "old"' in result
        assert "git" not in result.replace("# name", "")

    def test_preserves_other_content(self, tmp_path: Path) -> None:
        """Non-require sections and comments are preserved."""
        (tmp_path / "lakefile.toml").write_text(
            '[package]\nname = "myproject"\nversion = "0.1"\n\n'
            "# A dependency\n"
            '[[require]]\nname = "mathlib"\n'
            'scope = "leanprover-community"\n'
            'branch = "master"\n\n'
            "[leanOptions]\npp.unicode = true\n"
        )
        _rewrite_lakefile_toml_deps(tmp_path)
        result = (tmp_path / "lakefile.toml").read_text()
        assert 'name = "myproject"' in result
        assert "version" in result
        assert "# A dependency" in result
        assert 'path = ".lake/packages/mathlib"' in result
        assert "pp.unicode" in result


# ---------------------------------------------------------------------------
# Zip round-trip: symlinks, permissions, and duplicate-entry rejection
# ---------------------------------------------------------------------------

@pytest.mark.skipif(sys.platform == "win32", reason="Unix symlinks only")
class TestZipRoundTrip:
    """Test that create_zip and _safe_extract_zip preserve symlinks and permissions."""

    def test_symlink_and_permissions_round_trip(self, tmp_path: Path) -> None:
        from bundle import create_zip
        from download import _safe_extract_zip

        # Build a source tree with a regular file, an executable, and a symlink
        src = tmp_path / "bundle" / "pkg"
        src.mkdir(parents=True)
        (src / "data.txt").write_text("hello")
        (src / "run.sh").write_text("#!/bin/sh\necho hi")
        (src / "run.sh").chmod(0o755)
        (src / "link").symlink_to("data.txt")

        # Round-trip through zip
        zip_path = tmp_path / "out.zip"
        create_zip(tmp_path / "bundle", zip_path)

        dst = tmp_path / "extracted"
        dst.mkdir()
        with zipfile.ZipFile(zip_path) as zf:
            _safe_extract_zip(zf, dst)

        # create_zip stores paths relative to bundle_dir.parent,
        # so the extracted tree has bundle/pkg/...
        pkg = dst / "bundle" / "pkg"

        # Verify contents
        assert (pkg / "data.txt").read_text() == "hello"
        assert (pkg / "run.sh").read_text() == "#!/bin/sh\necho hi"

        # Verify symlink
        link = pkg / "link"
        assert link.is_symlink(), "link should be a symlink"
        assert os.readlink(link) == "data.txt"

        # Verify executable permission
        assert os.access(pkg / "run.sh", os.X_OK), "run.sh should be executable"

    def test_duplicate_entry_rejected(self, tmp_path: Path) -> None:
        """_safe_extract_zip must reject zips with duplicate filenames."""
        import io
        from download import _safe_extract_zip

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("dup.txt", "first")
            zf.writestr("dup.txt", "second")
        buf.seek(0)

        dest = tmp_path / "out"
        dest.mkdir()
        with pytest.raises(ValueError, match="Duplicate zip entry"):
            with zipfile.ZipFile(buf) as zf:
                _safe_extract_zip(zf, dest)

    def test_symlink_escape_rejected(self, tmp_path: Path) -> None:
        """_safe_extract_zip must reject symlinks that point outside dest."""
        import io
        from download import _safe_extract_zip

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            info = zipfile.ZipInfo("escape")
            info.external_attr = (0o120755 << 16)
            zf.writestr(info, "/etc/passwd")
        buf.seek(0)

        dest = tmp_path / "out"
        dest.mkdir()
        with pytest.raises(ValueError, match="Symlink would point outside"):
            with zipfile.ZipFile(buf) as zf:
                _safe_extract_zip(zf, dest)
