"""Compute the transitive import closure for a Lean 4 project.

Uses `lean --src-deps` for dependency discovery.
"""

import subprocess
from collections import deque
from pathlib import Path


def module_to_relpath(mod: str) -> Path:
    """Convert a module name to a relative file path.

    E.g. "Mathlib.Algebra.Group.Basic" -> Path("Mathlib/Algebra/Group/Basic.lean")
    """
    return Path(*mod.split(".")).with_suffix(".lean")


def compute_src_deps(project_dir: Path) -> set[Path]:
    """Compute transitive source dependencies using `lean --src-deps`.

    Returns a set of absolute Paths to .lean files.
    """
    deps: set[Path] = set()
    seen: set[Path] = set()
    queue: deque[Path] = deque()

    for lean_file in sorted(project_dir.rglob("*.lean")):
        if ".lake" not in lean_file.parts:
            queue.append(lean_file.resolve())

    while queue:
        lean_file = queue.popleft()
        if lean_file in seen:
            continue
        seen.add(lean_file)

        result = subprocess.run(
            ["lake", "env", "lean", "--src-deps", str(lean_file)],
            cwd=project_dir,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            raise RuntimeError(
                "lean --src-deps failed. Ensure the project toolchain is Lean 4.17+ "
                f"and lake is available.\n{stderr}"
            )
        for line in result.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            path = Path(line)
            if not path.is_absolute():
                path = (project_dir / path).resolve()
            if path in deps:
                continue
            deps.add(path)
            queue.append(path)

    return deps


def module_build_artifact_prefix(mod: str) -> str:
    """Return the relative path prefix for a module's build artifacts.

    E.g. "Mathlib.Algebra.Group.Basic" -> "Mathlib/Algebra/Group/Basic"
    """
    return str(Path(*mod.split(".")))


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
