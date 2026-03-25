from pathlib import Path

import json

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

    setup_vscodium_portable(vscodium_dir, extension_dir, settings_template)

    ext_dest = vscodium_dir / "data" / "extensions" / extension_dir.name
    assert (ext_dest / "package.json").is_file()
    assert not (ext_dest / "extension" / "package.json").exists()
