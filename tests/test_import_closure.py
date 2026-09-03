"""Tests for import_closure.py."""

from pathlib import Path

import pytest

import sys
sys.path.insert(0, str(Path(__file__).parent.parent))

from import_closure import (
    _LeanEnvironment,
    _src_deps_batch,
    _src_deps_one,
    module_to_relpath,
    compute_src_deps,
    find_module_build_artifacts,
    module_build_artifact_prefix,
    src_paths_to_module_stems,
)


class TestModuleToRelpath:
    def test_simple(self):
        assert module_to_relpath("Foo") == Path("Foo.lean")

    def test_dotted(self):
        assert module_to_relpath("Mathlib.Algebra.Group.Basic") == Path(
            "Mathlib/Algebra/Group/Basic.lean"
        )


class TestComputeSrcDeps:
    @staticmethod
    def lean_env(tmp_path: Path) -> _LeanEnvironment:
        return _LeanEnvironment(
            executable="/toolchain/bin/lean",
            process_env={"LEAN_PATH": "/build/lib/lean"},
            src_search_path=(tmp_path,),
        )

    def test_transitive_walks_batched_import_graph(self, tmp_path, monkeypatch):
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
            main.resolve(): [dep_a.resolve()],
            dep_a.resolve(): [dep_b.resolve()],
            dep_b.resolve(): [],
        }
        waves = []

        def fake_batch(lean_files, *args, **kwargs):
            waves.append(lean_files)
            return [outputs[path] for path in lean_files]

        monkeypatch.setattr(
            "import_closure._load_lean_environment",
            lambda _project: self.lean_env(tmp_path),
        )
        monkeypatch.setattr("import_closure._src_deps_batch", fake_batch)

        deps = compute_src_deps(project)

        assert deps == {dep_a.resolve(), dep_b.resolve()}
        assert waves == [[main.resolve()], [dep_a.resolve()], [dep_b.resolve()]]

    def test_reports_per_wave_progress(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        main = project / "Main.lean"
        main.write_text("import A\n")
        dep_a = tmp_path / "A.lean"
        dep_a.write_text("")

        outputs = {
            main.resolve(): [dep_a.resolve()],
            dep_a.resolve(): [],
        }
        monkeypatch.setattr(
            "import_closure._load_lean_environment",
            lambda _project: self.lean_env(tmp_path),
        )
        monkeypatch.setattr(
            "import_closure._src_deps_batch",
            lambda lean_files, *args, **kwargs: [
                outputs[path] for path in lean_files
            ],
        )
        events = []

        compute_src_deps(project, progress=lambda *event: events.append(event))

        assert events == [
            (1, 0, 1, 0, 0),
            (1, 1, 1, 1, 1),
            (2, 0, 1, 1, 1),
            (2, 1, 1, 2, 1),
        ]

    def test_falls_back_to_parallel_src_deps(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        main = project / "Main.lean"
        main.write_text("import A\n")
        dep_a = tmp_path / "A.lean"
        dep_a.write_text("")

        outputs = {
            main.resolve(): [dep_a.resolve()],
            dep_a.resolve(): [],
        }
        lean_env = self.lean_env(tmp_path)
        monkeypatch.setattr(
            "import_closure._load_lean_environment",
            lambda _project: lean_env,
        )
        monkeypatch.setattr(
            "import_closure._src_deps_batch",
            lambda *args, **kwargs: None,
        )
        monkeypatch.setattr(
            "import_closure._src_deps_one",
            lambda source, _project, lean_env=None: outputs[source],
        )
        deps = compute_src_deps(project, max_workers=1)

        assert deps == {dep_a.resolve()}

    def test_batch_resolves_json_imports(self, tmp_path, monkeypatch):
        project = tmp_path / "project"
        project.mkdir()
        main = project / "Main.lean"
        main.write_text("import A\n")
        source_root = tmp_path / "sources"
        source_root.mkdir()
        dep_a = source_root / "A.lean"
        dep_a.write_text("")
        lean_env = _LeanEnvironment(
            executable="/toolchain/bin/lean",
            process_env={"LEAN_PATH": "/build/lib/lean"},
            src_search_path=(source_root,),
        )
        calls = []

        class FakeResult:
            returncode = 0
            stdout = (
                '{"imports":[{"errors":[],"result":{"imports":'
                '[{"module":"A"}],"isModule":false}}]}'
            )

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return FakeResult()

        monkeypatch.setattr("import_closure.subprocess.run", fake_run)

        result = _src_deps_batch([main], project, lean_env)

        assert result == [[dep_a.resolve()]]
        assert calls[0][0] == [
            "/toolchain/bin/lean", "--deps-json", "--stdin",
        ]
        assert calls[0][1]["input"] == f"{main}\n"
        assert calls[0][1]["env"] is lean_env.process_env

    def test_src_deps_fallback_reuses_loaded_environment(
        self, tmp_path, monkeypatch,
    ):
        project = tmp_path / "project"
        project.mkdir()
        main = project / "Main.lean"
        main.write_text("")
        dep = tmp_path / "A.lean"
        dep.write_text("")
        lean_env = self.lean_env(tmp_path)
        calls = []

        class FakeResult:
            returncode = 0
            stderr = ""
            stdout = f"{dep}\n"

        def fake_run(cmd, **kwargs):
            calls.append((cmd, kwargs))
            return FakeResult()

        monkeypatch.setattr("import_closure.subprocess.run", fake_run)

        result = _src_deps_one(main, project, lean_env=lean_env)

        assert result == [dep.resolve()]
        assert calls[0][0] == [
            "/toolchain/bin/lean", "--src-deps", str(main),
        ]
        assert calls[0][1]["env"] is lean_env.process_env


class TestSrcPathsToModuleStems:
    def test_includes_project_sources(self, tmp_path):
        project = tmp_path / "project"
        (project / "MyProject").mkdir(parents=True)
        (project / "MyProject" / "Foo.lean").write_text("")
        (project / "Main.lean").write_text("")

        stems = src_paths_to_module_stems(set(), project)
        assert "MyProject/Foo" in stems
        assert "Main" in stems

    def test_includes_dependency_sources(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "Main.lean").write_text("")

        pkg = project / ".lake" / "packages" / "mathlib"
        (pkg / "Mathlib" / "Algebra" / "Group").mkdir(parents=True)
        (pkg / "Mathlib" / "Algebra" / "Group" / "Basic.lean").write_text("")

        dep_path = (pkg / "Mathlib" / "Algebra" / "Group" / "Basic.lean").resolve()
        stems = src_paths_to_module_stems({dep_path}, project)
        assert "Mathlib/Algebra/Group/Basic" in stems
        assert "Main" in stems  # project source also included

    def test_resolves_dependency_src_dir_against_build_artifacts(self, tmp_path):
        """Verso-style srcDir prefixes must not leak into artifact stems."""
        project = tmp_path / "project"
        project.mkdir()
        (project / "Main.lean").write_text("")

        package = project / ".lake" / "packages" / "verso"
        source = (
            package
            / "src" / "verso-manual"
            / "VersoManual" / "Html" / "Style.lean"
        )
        source.parent.mkdir(parents=True)
        source.write_text("")

        artifact = (
            package
            / ".lake" / "build" / "lib" / "lean"
            / "VersoManual" / "Html" / "Style.olean"
        )
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"olean")

        stems = src_paths_to_module_stems({source.resolve()}, project)

        assert "VersoManual/Html/Style" in stems
        assert "src/verso-manual/VersoManual/Html/Style" not in stems

    def test_resolves_project_src_dir_against_build_artifacts(self, tmp_path):
        project = tmp_path / "project"
        source = project / "src" / "Course" / "Main.lean"
        source.parent.mkdir(parents=True)
        source.write_text("")

        artifact = (
            project / ".lake" / "build" / "lib" / "lean"
            / "Course" / "Main.olean"
        )
        artifact.parent.mkdir(parents=True)
        artifact.write_bytes(b"olean")

        stems = src_paths_to_module_stems(set(), project)

        assert "Course/Main" in stems
        assert "src/Course/Main" not in stems

    def test_ignores_toolchain_sources(self, tmp_path):
        project = tmp_path / "project"
        project.mkdir()
        (project / "Main.lean").write_text("")

        toolchain_src = tmp_path / "toolchain" / "lib" / "Init" / "Prelude.lean"
        toolchain_src.parent.mkdir(parents=True)
        toolchain_src.write_text("")

        stems = src_paths_to_module_stems({toolchain_src.resolve()}, project)
        # Toolchain source is not under project or .lake/packages, so ignored
        assert "Init/Prelude" not in stems
        assert "Main" in stems

    def test_skips_lake_dir_in_project_scan(self, tmp_path):
        project = tmp_path / "project"
        lake_build = project / ".lake" / "build" / "lib" / "lean"
        lake_build.mkdir(parents=True)
        (lake_build / "Stale.lean").write_text("")
        (project / "Real.lean").write_text("")

        stems = src_paths_to_module_stems(set(), project)
        assert "Real" in stems
        # Files inside .lake/ are NOT treated as project sources
        assert "Stale" not in stems


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
