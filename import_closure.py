"""Compute the transitive import closure for a Lean 4 project.

Uses Lean's batched ``--deps-json`` parser when available, with parallel
``--src-deps`` calls as a compatibility fallback for older toolchains.
Lake's environment is computed once instead of reloading the full workspace
for every source file.
"""

import json
import os
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path


ProgressCallback = Callable[[int, int, int, int, int], None]


@dataclass(frozen=True)
class _LeanEnvironment:
    """Lean executable and search paths produced by one ``lake env`` call."""

    executable: str
    process_env: dict[str, str]
    src_search_path: tuple[Path, ...]


def module_to_relpath(mod: str) -> Path:
    """Convert a module name to a relative file path.

    E.g. "Mathlib.Algebra.Group.Basic" -> Path("Mathlib/Algebra/Group/Basic.lean")
    """
    return Path(*mod.split(".")).with_suffix(".lean")


def _load_lean_environment(
    project_dir: Path,
    lake_executable: str | Path = "lake",
) -> _LeanEnvironment:
    """Load Lake's process environment and Lean source path once."""
    base_env = os.environ.copy()
    lake_path = Path(lake_executable)
    if lake_path.is_absolute():
        lean_bin = lake_path.parent
        base_env["PATH"] = (
            str(lean_bin) + os.pathsep + base_env.get("PATH", "")
        )
        base_env["ELAN_HOME"] = str(lean_bin.parent)

    result = subprocess.run(
        [str(lake_executable), "env"],
        cwd=project_dir,
        env=base_env,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(f"lake env failed.\n{stderr}")

    lake_env: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" not in line:
            continue
        name, value = line.split("=", 1)
        lake_env[name] = value

    executable = lake_env.get("LEAN")
    if not executable:
        raise RuntimeError("lake env did not report the LEAN executable")

    process_env = base_env
    process_env.update(lake_env)

    src_search_path: list[Path] = []
    for entry in lake_env.get("LEAN_SRC_PATH", "").split(os.pathsep):
        if not entry:
            continue
        path = Path(entry)
        if not path.is_absolute():
            path = project_dir / path
        path = path.resolve()
        if path not in src_search_path:
            src_search_path.append(path)

    # Lean.getSrcSearchPath appends these built-in source roots after
    # LEAN_SRC_PATH. Include them so --deps-json resolves exactly the same
    # modules as --src-deps, including Init and Lake sources.
    sysroot = lake_env.get("LEAN_SYSROOT")
    lean_src = (
        Path(sysroot) / "src" / "lean"
        if sysroot
        else Path(executable).resolve().parent.parent / "src" / "lean"
    )
    for path in (lean_src / "lake", lean_src):
        path = path.resolve()
        if path not in src_search_path:
            src_search_path.append(path)

    return _LeanEnvironment(
        executable=executable,
        process_env=process_env,
        src_search_path=tuple(src_search_path),
    )


def _src_deps_one(lean_file: Path, project_dir: Path,
                  timeout: int = 60,
                  lean_env: _LeanEnvironment | None = None) -> list[Path]:
    """Run ``lean --src-deps`` on a single file and return resolved paths."""
    if lean_env is None:
        command = ["lake", "env", "lean"]
        process_env = None
    else:
        command = [lean_env.executable]
        process_env = lean_env.process_env

    result = subprocess.run(
        [*command, "--src-deps", str(lean_file)],
        cwd=project_dir,
        env=process_env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if result.returncode != 0:
        stderr = (result.stderr or "").strip()
        raise RuntimeError(
            "lean --src-deps failed. Ensure the project toolchain is Lean 4.17+ "
            f"and lake is available.\n{stderr}"
        )
    paths: list[Path] = []
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        path = Path(line)
        if not path.is_absolute():
            path = (project_dir / path).resolve()
        else:
            path = path.resolve()
        paths.append(path)
    return paths


def _resolve_module_source(
    module: str,
    src_search_path: tuple[Path, ...],
) -> Path | None:
    """Resolve a module name using Lean's ordered source search path."""
    relative = module_to_relpath(module)
    for root in src_search_path:
        candidate = root / relative
        if candidate.is_file():
            return candidate.resolve()
    return None


def _src_deps_batch(
    lean_files: list[Path],
    project_dir: Path,
    lean_env: _LeanEnvironment,
    module_sources: dict[str, Path] | None = None,
    timeout: int = 60,
) -> list[list[Path]] | None:
    """Parse direct imports for a wave in one Lean process.

    Returns one dependency list per input file. ``None`` means the batched
    mode is unavailable or could not parse the wave, in which case callers
    should use the compatibility path. ``--deps-json`` was added in Lean
    4.22, so falling back is expected for older supported toolchains.
    """
    if module_sources is None:
        module_sources = {}

    try:
        result = subprocess.run(
            [lean_env.executable, "--deps-json", "--stdin"],
            cwd=project_dir,
            env=lean_env.process_env,
            input="\n".join(str(path) for path in lean_files) + "\n",
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None

    if result.returncode != 0:
        return None

    try:
        parsed = json.loads(result.stdout)["imports"]
        if len(parsed) != len(lean_files):
            return None

        dependencies: list[list[Path]] = []
        for item in parsed:
            if item.get("errors") or item.get("result") is None:
                return None
            paths: list[Path] = []
            for imported in item["result"]["imports"]:
                module = imported["module"]
                source = module_sources.get(module)
                if source is None:
                    source = _resolve_module_source(
                        module, lean_env.src_search_path,
                    )
                    if source is None:
                        return None
                    module_sources[module] = source
                paths.append(source)
            dependencies.append(paths)
        return dependencies
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None


def compute_src_deps(project_dir: Path,
                     max_workers: int = 16,
                     progress: ProgressCallback | None = None,
                     lake_executable: str | Path | None = None) -> set[Path]:
    """Compute transitive source dependencies from the project's sources.

    Returns a set of absolute Paths to ``.lean`` files that are
    transitively imported by the project's own sources.

    On Lean 4.22+, each BFS wave is parsed in one process with
    ``lean --deps-json --stdin``. Older toolchains, batch parse failures, and
    unusual source layouts automatically fall back to parallel
    ``lean --src-deps`` calls. Both paths use a single precomputed Lake
    environment, avoiding a full workspace reload for every source file.

    If *progress* is provided, it is called with ``(wave, completed,
    wave_total, checked_total, dependencies_found)`` before each wave and
    after each file completes.

    """
    deps: set[Path] = set()
    seen: set[Path] = set()
    checked = 0
    wave_number = 0

    # Seed: the project's own .lean files (outside .lake/).
    wave: list[Path] = []
    for lean_file in sorted(project_dir.rglob("*.lean")):
        if ".lake" not in lean_file.parts:
            resolved = lean_file.resolve()
            if resolved not in seen:
                seen.add(resolved)
                wave.append(resolved)

    if not wave:
        return deps

    if lake_executable is None:
        lean_env = _load_lean_environment(project_dir)
    else:
        lean_env = _load_lean_environment(project_dir, lake_executable)
    batch_enabled = True
    module_sources: dict[str, Path] = {}

    while wave:
        wave_number += 1
        wave_total = len(wave)
        wave_completed = 0
        next_wave: list[Path] = []

        if progress is not None:
            progress(wave_number, 0, wave_total, checked, len(deps))

        batch_results = (
            _src_deps_batch(
                wave, project_dir, lean_env, module_sources,
            )
            if batch_enabled
            else None
        )
        if batch_results is not None:
            for dep_paths in batch_results:
                for dep_path in dep_paths:
                    deps.add(dep_path)
                    if dep_path not in seen:
                        seen.add(dep_path)
                        next_wave.append(dep_path)
                checked += 1
                wave_completed += 1
                if progress is not None:
                    progress(
                        wave_number, wave_completed, wave_total,
                        checked, len(deps),
                    )
        else:
            # ``--deps-json`` is unavailable before Lean 4.22. It may also
            # reject an unusual header which the full parser accepts. Disable
            # batching after the first failure and preserve the old behavior.
            batch_enabled = False
            with ThreadPoolExecutor(
                max_workers=min(max_workers, len(wave)),
            ) as pool:
                futures = {
                    pool.submit(
                        _src_deps_one, f, project_dir,
                        lean_env=lean_env,
                    ): f
                    for f in wave
                }
                for future in as_completed(futures):
                    dep_paths = future.result()
                    for dep_path in dep_paths:
                        deps.add(dep_path)
                        if dep_path not in seen:
                            seen.add(dep_path)
                            next_wave.append(dep_path)
                    checked += 1
                    wave_completed += 1
                    if progress is not None:
                        progress(
                            wave_number, wave_completed, wave_total,
                            checked, len(deps),
                        )

        wave = next_wave

    return deps


def module_build_artifact_prefix(mod: str) -> str:
    """Return the relative path prefix for a module's build artifacts.

    E.g. "Mathlib.Algebra.Group.Basic" -> "Mathlib/Algebra/Group/Basic"
    """
    return Path(*mod.split(".")).as_posix()


def _resolve_built_module_stem(build_dir: Path, source_rel: Path) -> str:
    """Resolve a source path to the module stem used by Lake's build tree.

    Lake stores artifacts by *module name*, not by the path relative to the
    package root.  Those are the same for the common layout::

        Mathlib/Foo.lean -> build/lib/lean/Mathlib/Foo.olean

    but differ when a library declares ``srcDir``::

        src/verso-manual/VersoManual/Foo.lean
          -> build/lib/lean/VersoManual/Foo.olean

    We deliberately resolve this against the artifacts of the already-built
    project instead of trying to parse arbitrary Lake DSL.  A module path is
    a suffix of its source path after removing the library's ``srcDir``.  Try
    suffixes longest-first so nested module names win over coincidental short
    names such as ``Foo.olean``.

    If no artifact exists, return the unmodified source-relative stem.  This
    preserves the old behaviour for source files that are not build targets;
    the caller's bundle-level validation can still fall back to a full copy
    when none of the computed stems match build artifacts.
    """
    source_stem = source_rel.with_suffix("")
    parts = source_stem.parts

    if build_dir.is_dir():
        for offset in range(len(parts)):
            candidate = Path(*parts[offset:])
            if (build_dir / f"{candidate}.olean").is_file():
                return candidate.as_posix()

    return source_stem.as_posix()


def src_paths_to_module_stems(
    needed_srcs: set[Path],
    project_dir: Path,
) -> set[str]:
    """Convert source file paths to module stems for build artifact filtering.

    A module stem is the slash-separated path relative to its package
    (or project) root without the ``.lean`` extension.
    E.g. ``Mathlib/Algebra/Group/Basic``.

    These stems match the relative paths of build artifacts under
    ``build/lib/lean/`` directories.

    *needed_srcs* should come from :func:`compute_src_deps` (transitive
    dependencies only). The project's own modules are discovered by
    scanning *project_dir* for ``.lean`` files outside ``.lake/``.

    Source paths are resolved against each package's existing build tree, so
    libraries using Lake's ``srcDir`` option are handled without parsing the
    package's Lake configuration.
    """
    project_dir = project_dir.resolve()
    lake_packages = project_dir / ".lake" / "packages"
    stems: set[str] = set()

    # Include the project's own modules (always needed).
    for lean_file in project_dir.rglob("*.lean"):
        try:
            rel = lean_file.relative_to(project_dir)
        except ValueError:
            continue
        if ".lake" in rel.parts:
            continue
        stems.add(_resolve_built_module_stem(
            project_dir / ".lake" / "build" / "lib" / "lean", rel,
        ))

    # Include dependency modules from the import closure.
    for src in needed_srcs:
        src = src.resolve()
        if not str(src).endswith(".lean"):
            continue
        # Package source: .lake/packages/<pkg>/<module_path>.lean
        try:
            rel = src.relative_to(lake_packages)
            parts = rel.parts
            if len(parts) >= 2:
                # parts[0] is the package name. The remainder is the source
                # path relative to that package, which may still include a
                # library srcDir such as ``src/verso-manual``.
                package_dir = lake_packages / parts[0]
                build_dir = package_dir / ".lake" / "build" / "lib" / "lean"
                stems.add(_resolve_built_module_stem(
                    build_dir, Path(*parts[1:]),
                ))
            continue
        except ValueError:
            pass
        # Project source already handled above; toolchain sources
        # (e.g. Init/Prelude.lean under ~/.elan/) live outside .lake/
        # and are copied with the lean toolchain directory, not here.

    return stems


def find_module_build_artifacts(mod: str, build_dir: Path) -> list[tuple[str, Path]]:
    """Find all build artifacts for a module in a build directory.

    Returns list of (relative_path, absolute_path) for each artifact found.
    Catches all file types (.olean, .ilean, .olean.private, .ir, .trace, etc.)
    without needing to enumerate them.
    """
    prefix = module_build_artifact_prefix(mod)
    parent = build_dir / Path(prefix).parent
    stem = Path(prefix).name

    if not parent.is_dir():
        return []

    results = []
    for f in parent.iterdir():
        if f.is_file() and f.name.startswith(stem + "."):
            rel = f.relative_to(build_dir).as_posix()
            results.append((rel, f))
    return results
