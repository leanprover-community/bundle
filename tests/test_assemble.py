import os
import sys
import zipfile
from pathlib import Path

import json
import pytest

from assemble import classify_dep_source, setup_vscodium_portable


def test_classify_dep_source_package() -> None:
    project_dir = Path("/tmp/project")
    packages_dir = project_dir / ".lake" / "packages"
    toolchain_lib = Path("/tmp/lean/lib/lean")
    src = packages_dir / "mathlib" / "Mathlib" / "Data" / "Nat" / "Basic.lean"

    assert classify_dep_source(src, project_dir, packages_dir, toolchain_lib) == (
        "Mathlib.Data.Nat.Basic",
        "mathlib",
    )


def test_classify_dep_source_toolchain() -> None:
    project_dir = Path("/tmp/project")
    packages_dir = project_dir / ".lake" / "packages"
    toolchain_lib = Path("/tmp/lean/lib/lean")
    src = toolchain_lib / "Std" / "Data" / "HashMap.lean"

    assert classify_dep_source(src, project_dir, packages_dir, toolchain_lib) == (
        "Std.Data.HashMap",
        "_toolchain",
    )


def test_classify_dep_source_project() -> None:
    project_dir = Path("/tmp/project")
    packages_dir = project_dir / ".lake" / "packages"
    toolchain_lib = Path("/tmp/lean/lib/lean")
    src = project_dir / "MyProject" / "Main.lean"

    assert classify_dep_source(src, project_dir, packages_dir, toolchain_lib) == (
        "MyProject.Main",
        None,
    )


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
