"""Tests for import_closure.py."""

import textwrap
from pathlib import Path

import pytest

# Add parent directory to path for imports
import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from import_closure import (
    parse_imports,
    module_to_relpath,
    find_module_source,
    compute_closure,
    compute_src_deps,
    find_module_build_artifacts,
    module_build_artifact_prefix,
)


@pytest.fixture
def tmp_lean_files(tmp_path):
    """Create a mock Lean project structure for testing."""
    # Project file
    project = tmp_path / "project"
    project.mkdir()
    (project / "Main.lean").write_text(textwrap.dedent("""\
        import MyLib.Basic
        import MyLib.Advanced

        def main : IO Unit := pure ()
    """))

    # Project library files
    mylib = project / "MyLib"
    mylib.mkdir()
    (mylib / "Basic.lean").write_text(textwrap.dedent("""\
        import Dep.Core

        def basic := 42
    """))
    (mylib / "Advanced.lean").write_text(textwrap.dedent("""\
        import MyLib.Basic
        import Dep.Extra

        def advanced := basic + 1
    """))

    # Dependency package
    dep = tmp_path / "dep"
    dep.mkdir()
    dep_src = dep / "Dep"
    dep_src.mkdir()
    (dep_src / "Core.lean").write_text(textwrap.dedent("""\
        def core := "hello"
    """))
    (dep_src / "Extra.lean").write_text(textwrap.dedent("""\
        import Dep.Core

        def extra := core ++ " world"
    """))
    (dep_src / "Unused.lean").write_text(textwrap.dedent("""\
        def unused := "should not appear"
    """))

    return tmp_path


class TestParseImports:
    def test_basic_imports(self, tmp_path):
        f = tmp_path / "test.lean"
        f.write_text("import Foo.Bar\nimport Baz\n\ndef x := 1")
        assert parse_imports(f) == ["Foo.Bar", "Baz"]

    def test_public_import(self, tmp_path):
        f = tmp_path / "test.lean"
        f.write_text("public import Foo.Bar\n")
        assert parse_imports(f) == ["Foo.Bar"]

    def test_meta_import(self, tmp_path):
        f = tmp_path / "test.lean"
        f.write_text("meta import Foo.Bar\npublic meta import Baz\n")
        assert parse_imports(f) == ["Foo.Bar", "Baz"]

    def test_no_imports(self, tmp_path):
        f = tmp_path / "test.lean"
        f.write_text("def x := 1\n")
        assert parse_imports(f) == []

    def test_nonexistent_file(self, tmp_path):
        f = tmp_path / "nonexistent.lean"
        assert parse_imports(f) == []

    def test_import_with_leading_whitespace(self, tmp_path):
        f = tmp_path / "test.lean"
        f.write_text("  import Foo\n")
        assert parse_imports(f) == ["Foo"]

    def test_multiple_imports(self, tmp_path):
        f = tmp_path / "test.lean"
        f.write_text("import A\nimport B.C\nimport D.E.F\n")
        assert parse_imports(f) == ["A", "B.C", "D.E.F"]


class TestModuleToRelpath:
    def test_simple(self):
        assert module_to_relpath("Foo") == Path("Foo.lean")

    def test_dotted(self):
        assert module_to_relpath("Mathlib.Algebra.Group.Basic") == Path(
            "Mathlib/Algebra/Group/Basic.lean"
        )


class TestFindModuleSource:
    def test_found(self, tmp_lean_files):
        paths = [tmp_lean_files / "dep"]
        result = find_module_source("Dep.Core", paths)
        assert result is not None
        assert result.name == "Core.lean"

    def test_not_found(self, tmp_lean_files):
        paths = [tmp_lean_files / "dep"]
        result = find_module_source("Nonexistent.Module", paths)
        assert result is None


class TestComputeClosure:
    def test_transitive_closure(self, tmp_lean_files):
        project = tmp_lean_files / "project"
        search_paths = [project, tmp_lean_files / "dep"]

        closure = compute_closure(project, search_paths)

        # Should include all transitively imported modules
        assert "MyLib.Basic" in closure
        assert "MyLib.Advanced" in closure
        assert "Dep.Core" in closure
        assert "Dep.Extra" in closure

        # Should NOT include unused modules
        assert "Dep.Unused" not in closure

    def test_no_duplicates(self, tmp_lean_files):
        """Dep.Core is imported by both MyLib.Basic and Dep.Extra."""
        project = tmp_lean_files / "project"
        search_paths = [project, tmp_lean_files / "dep"]

        closure = compute_closure(project, search_paths)

        # Should be a set (no duplicates by construction)
        assert isinstance(closure, set)
        assert "Dep.Core" in closure

    def test_exclude_prefixes(self, tmp_lean_files):
        project = tmp_lean_files / "project"
        search_paths = [project, tmp_lean_files / "dep"]

        closure = compute_closure(
            project, search_paths, exclude_prefixes=("Dep.",)
        )

        assert "MyLib.Basic" in closure
        assert "MyLib.Advanced" in closure
        assert "Dep.Core" not in closure
        assert "Dep.Extra" not in closure


class TestComputeSrcDeps:
    def test_transitive_walks_src_deps_graph(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        main = project / "Main.lean"
        main.write_text("import A\n")

        dep_a = tmp_path / "deps" / "A.lean"
        dep_a.parent.mkdir(parents=True)
        dep_a.write_text("import B\n")

        dep_b = tmp_path / "deps" / "B.lean"
        dep_b.write_text("def b := 1\n")

        outputs = {
            str(main.resolve()): f"{dep_a}\n",
            str(dep_a.resolve()): f"{dep_b}\n",
            str(dep_b.resolve()): "",
        }

        class FakeResult:
            def __init__(self, stdout: str):
                self.stdout = stdout
                self.stderr = ""
                self.returncode = 0

        def fake_run(cmd, cwd, capture_output, text):
            return FakeResult(outputs[str(Path(cmd[-1]).resolve())])

        monkeypatch.setattr("import_closure.subprocess.run", fake_run)

        deps = compute_src_deps(project)

        assert deps == {dep_a.resolve(), dep_b.resolve()}


class TestModuleBuildArtifacts:
    def test_prefix(self):
        assert module_build_artifact_prefix("Mathlib.Algebra.Group.Basic") == "Mathlib/Algebra/Group/Basic"

    def test_find_artifacts(self, tmp_path):
        build_dir = tmp_path / "build"
        (build_dir / "Foo" / "Bar").mkdir(parents=True)
        (build_dir / "Foo" / "Bar" / "Baz.olean").write_bytes(b"")
        (build_dir / "Foo" / "Bar" / "Baz.ilean").write_bytes(b"")
        (build_dir / "Foo" / "Bar" / "Baz.olean.private").write_bytes(b"")
        (build_dir / "Foo" / "Bar" / "Baz.ir").write_bytes(b"")
        (build_dir / "Foo" / "Bar" / "Other.olean").write_bytes(b"")  # different module

        artifacts = find_module_build_artifacts("Foo.Bar.Baz", build_dir)
        rel_paths = [r for r, _ in artifacts]
        assert "Foo/Bar/Baz.olean" in rel_paths
        assert "Foo/Bar/Baz.ilean" in rel_paths
        assert "Foo/Bar/Baz.olean.private" in rel_paths
        assert "Foo/Bar/Baz.ir" in rel_paths
        assert "Foo/Bar/Other.olean" not in rel_paths  # different module
