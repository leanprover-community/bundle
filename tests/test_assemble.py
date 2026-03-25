from pathlib import Path

from assemble import classify_dep_source


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
