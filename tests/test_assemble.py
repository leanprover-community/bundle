import os
import subprocess
import sys
import zipfile
from pathlib import Path

import json
import pytest

from assemble import (
    _module_stem_from_build_path,
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

    setup_vscodium_portable(vscodium_dir, [extension_dir], settings_template)

    ext_dest = vscodium_dir / "data" / "extensions" / extension_dir.name
    assert (ext_dest / "package.json").is_file()
    assert not (ext_dest / "extension" / "package.json").exists()


# ---------------------------------------------------------------------------
# Lakefile.lean rewriting: bare, quoted, and guillemet require syntax
# ---------------------------------------------------------------------------

class TestRewriteLakefileLeanDeps:
    """Test _rewrite_lakefile_lean_deps handles all require name forms."""

    def test_bare_require(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        lakefile.write_text('require mathlib from git "https://github.com/leanprover-community/mathlib4" @ "v1.0"\n')
        _rewrite_lakefile_lean_deps(tmp_path)
        assert lakefile.read_text() == 'require mathlib from ".lake/packages/mathlib"\n'

    def test_quoted_require(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        lakefile.write_text('require "mathlib" from git "https://github.com/leanprover-community/mathlib4" @ "v1.0"\n')
        _rewrite_lakefile_lean_deps(tmp_path)
        assert lakefile.read_text() == 'require "mathlib" from ".lake/packages/mathlib"\n'

    def test_guillemet_require(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        lakefile.write_text('require «doc-gen4» from git "https://github.com/leanprover/doc-gen4" @ "main"\n')
        _rewrite_lakefile_lean_deps(tmp_path)
        assert lakefile.read_text() == 'require «doc-gen4» from ".lake/packages/doc-gen4"\n'

    def test_no_rev(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        lakefile.write_text('require mathlib from git "https://github.com/leanprover-community/mathlib4"\n')
        _rewrite_lakefile_lean_deps(tmp_path)
        assert lakefile.read_text() == 'require mathlib from ".lake/packages/mathlib"\n'

    def test_multiple_deps(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        lakefile.write_text(
            'require "mathlib" from git "https://github.com/leanprover-community/mathlib4" @ "v1.0"\n'
            'require «doc-gen4» from git "https://github.com/leanprover/doc-gen4" @ "main"\n'
            'require aesop from git "https://github.com/leanprover-community/aesop"\n'
        )
        _rewrite_lakefile_lean_deps(tmp_path)
        expected = (
            'require "mathlib" from ".lake/packages/mathlib"\n'
            'require «doc-gen4» from ".lake/packages/doc-gen4"\n'
            'require aesop from ".lake/packages/aesop"\n'
        )
        assert lakefile.read_text() == expected

    def test_commented_out_require_not_rewritten(self, tmp_path: Path) -> None:
        lakefile = tmp_path / "lakefile.lean"
        original = '-- require mathlib from git "https://github.com/leanprover-community/mathlib4" @ "v1.0"\n'
        lakefile.write_text(original)
        _rewrite_lakefile_lean_deps(tmp_path)
        assert lakefile.read_text() == original

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
