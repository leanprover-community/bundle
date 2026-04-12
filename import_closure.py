"""Compute the transitive import closure for a Lean 4 project.

Uses ``lean --src-deps`` for dependency discovery, running invocations
in parallel to avoid the minutes-long sequential BFS that dominated
bundle build times on mathlib-based projects.
"""

import subprocess
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


def module_to_relpath(mod: str) -> Path:
    """Convert a module name to a relative file path.

    E.g. "Mathlib.Algebra.Group.Basic" -> Path("Mathlib/Algebra/Group/Basic.lean")
    """
    return Path(*mod.split(".")).with_suffix(".lean")


def _src_deps_one(lean_file: Path, project_dir: Path,
                  timeout: int = 60) -> list[Path]:
    """Run ``lean --src-deps`` on a single file and return resolved paths."""
    result = subprocess.run(
        ["lake", "env", "lean", "--src-deps", str(lean_file)],
        cwd=project_dir,
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


def compute_src_deps(project_dir: Path,
                     max_workers: int = 64) -> set[Path]:
    """Compute transitive source dependencies using ``lean --src-deps``.

    Returns a set of absolute Paths to ``.lean`` files that are
    transitively imported by the project's own sources.

    Runs ``lean --src-deps`` in parallel (up to *max_workers* at a time)
    to avoid the per-invocation ~500 ms overhead from dominating wall
    time.  BFS levels are processed in waves: all files discovered in
    one wave are queried concurrently, then the next wave starts.
    """
    deps: set[Path] = set()
    seen: set[Path] = set()

    # Seed: the project's own .lean files (outside .lake/).
    wave: list[Path] = []
    for lean_file in sorted(project_dir.rglob("*.lean")):
        if ".lake" not in lean_file.parts:
            resolved = lean_file.resolve()
            if resolved not in seen:
                seen.add(resolved)
                wave.append(resolved)

    while wave:
        next_wave: list[Path] = []

        with ThreadPoolExecutor(max_workers=min(max_workers, len(wave))) as pool:
            futures = {
                pool.submit(_src_deps_one, f, project_dir): f
                for f in wave
            }
            for future in as_completed(futures):
                for dep_path in future.result():
                    if dep_path not in seen:
                        seen.add(dep_path)
                        deps.add(dep_path)
                        next_wave.append(dep_path)

        wave = next_wave

    return deps


def module_build_artifact_prefix(mod: str) -> str:
    """Return the relative path prefix for a module's build artifacts.

    E.g. "Mathlib.Algebra.Group.Basic" -> "Mathlib/Algebra/Group/Basic"
    """
    return str(Path(*mod.split(".")))


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

    .. note:: This assumes the standard Lean project layout where source
       paths directly mirror module names. If a package uses Lake's
       ``srcDir`` option the computed stems will be wrong. Callers should
       validate the result against actual build artifacts.
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
        stems.add(str(rel.with_suffix("")))

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
                # parts[0] is the package name, rest is the module path
                stems.add(str(Path(*parts[1:]).with_suffix("")))
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
            rel = str(f.relative_to(build_dir))
            results.append((rel, f))
    return results
