"""Tests for import_closure.py."""

from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from import_closure import (
    module_to_relpath,
    compute_src_deps,
    find_module_build_artifacts,
    module_build_artifact_prefix,
)


class TestModuleToRelpath:
    def test_simple(self):
        assert module_to_relpath("Foo") == Path("Foo.lean")

    def test_dotted(self):
        assert module_to_relpath("Mathlib.Algebra.Group.Basic") == Path(
            "Mathlib/Algebra/Group/Basic.lean"
        )


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
