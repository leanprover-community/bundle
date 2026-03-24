"""Compute the transitive import closure for a Lean 4 project.

Prefer Lean's own dependency discovery via `lean --src-deps` to avoid
fragile Python parsing. Fallback helpers for parsing imports are kept
for tests and legacy behavior.
"""

import re
import subprocess
import os
from collections import deque
from pathlib import Path


# Matches all import variants:
#   import Foo.Bar
#   public import Foo.Bar
#   meta import Foo.Bar
#   public meta import Foo.Bar
#   @[...] import Foo.Bar
_IMPORT_RE = re.compile(
    r"^\s*(?:@\[.*?\]\s*)?(?:public\s+)?(?:meta\s+)?import\s+(\S+)\s*$"
)


def parse_imports(path: Path) -> list[str]:
    """Parse import statements from a .lean file.

    Returns a list of module names (e.g. ["Mathlib.Algebra.Group.Basic"]).
    Only looks at lines before the first non-import statement, matching
    Lean's own fast import parser behavior.
    """
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    modules: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        match = _IMPORT_RE.match(line)
        if not match:
            break
        mod = match.group(1)
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", mod):
            modules.append(mod)
    return modules


def module_to_relpath(mod: str) -> Path:
    """Convert a module name to a relative file path.

    E.g. "Mathlib.Algebra.Group.Basic" -> Path("Mathlib/Algebra/Group/Basic.lean")
    """
    return Path(*mod.split(".")).with_suffix(".lean")


def find_module_source(mod: str, search_paths: list[Path]) -> Path | None:
    """Find the .lean source file for a module name.

    Searches through the given paths in order.
    """
    rel = module_to_relpath(mod)
    for sp in search_paths:
        candidate = sp / rel
        if candidate.is_file():
            return candidate
    return None


def compute_closure(
    project_dir: Path,
    search_paths: list[Path],
    *,
    exclude_prefixes: tuple[str, ...] = (),
) -> set[str]:
    """Compute the transitive import closure for a project.

    Args:
        project_dir: Root directory of the project (contains .lean files).
        search_paths: Directories to search for dependency .lean sources.
            Should include the project dir, all package dirs, and the
            toolchain lib/lean dir.
        exclude_prefixes: Module name prefixes to exclude from the closure
            (e.g. ("Lake.",) to skip Lake modules).

    Returns:
        Set of module names transitively imported by the project.
    """
    needed: set[str] = set()
    queue: deque[str] = deque()

    # Seed: all imports from the project's own .lean files
    for lean_file in sorted(project_dir.rglob("*.lean")):
        # Skip anything inside .lake/
        if ".lake" in lean_file.parts:
            continue
        for mod in parse_imports(lean_file):
            if mod not in needed:
                queue.append(mod)

    # BFS through the import graph
    while queue:
        mod = queue.popleft()
        if mod in needed:
            continue
        if any(mod.startswith(p) for p in exclude_prefixes):
            continue
        needed.add(mod)

        source = find_module_source(mod, search_paths)
        if source is not None:
            for imp in parse_imports(source):
                if imp not in needed:
                    queue.append(imp)

    return needed


def build_search_paths(
    project_dir: Path,
    toolchain_lib: Path | None = None,
) -> list[Path]:
    """Build the list of search paths for a lake-based project.

    Looks at .lake/packages/ for dependency sources and optionally
    includes the toolchain's lib/lean/ directory for Init/Lean/Std.

    Args:
        project_dir: Root directory of the project.
        toolchain_lib: Path to toolchain's lib/lean/ directory (for Init, Lean, Std).

    Returns:
        Ordered list of search paths.
    """
    paths: list[Path] = [project_dir]

    # Add all package directories
    packages_dir = project_dir / ".lake" / "packages"
    if packages_dir.is_dir():
        for pkg in sorted(packages_dir.iterdir()):
            if pkg.is_dir():
                paths.append(pkg)

    # Add toolchain lib for Init, Lean, Std
    if toolchain_lib is not None and toolchain_lib.is_dir():
        paths.append(toolchain_lib)

    return paths


def get_lean_src_paths(project_dir: Path) -> list[Path]:
    """Return LEAN_SRC_PATH entries in Lake's environment (ordered).

    Uses `lake env python -c ...` to avoid shell-specific output formats.
    """
    result = subprocess.run(
        [
            "lake",
            "env",
            "python",
            "-c",
            "import os; print(os.environ.get('LEAN_SRC_PATH',''))",
        ],
        cwd=project_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    raw = result.stdout.strip()
    if not raw:
        return []
    paths: list[Path] = []
    for entry in raw.split(os.pathsep):
        if not entry:
            continue
        p = Path(entry)
        if not p.is_absolute():
            p = (project_dir / p).resolve()
        paths.append(p)
    return paths


def compute_src_deps(project_dir: Path) -> set[Path]:
    """Compute transitive source dependencies using `lean --src-deps`.

    Returns a set of absolute Paths to .lean files.
    """
    deps: set[Path] = set()
    for lean_file in sorted(project_dir.rglob("*.lean")):
        if ".lake" in lean_file.parts:
            continue
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
            deps.add(path)
    return deps


def module_build_artifact_prefix(mod: str) -> str:
    """Return the relative path prefix for a module's build artifacts.

    E.g. "Mathlib.Algebra.Group.Basic" -> "Mathlib/Algebra/Group/Basic"
    Use this to glob for all files starting with this prefix in a build dir.
    """
    return str(Path(*mod.split(".")))


def find_module_build_artifacts(mod: str, build_dir: Path) -> list[tuple[str, Path]]:
    """Find all build artifacts for a module in a build directory.

    Returns list of (relative_path, absolute_path) for each artifact found.
    This catches all file types (.olean, .ilean, .olean.private,
    .olean.server, .ir, .trace, .hash, .extra, etc.) without
    needing to enumerate them.
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
