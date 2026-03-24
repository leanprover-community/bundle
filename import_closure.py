"""Compute the transitive import closure for a Lean 4 project.

Given a project directory and the search paths for its dependencies,
determine exactly which modules are transitively imported.
"""

import re
from collections import deque
from pathlib import Path


# Matches all import variants:
#   import Foo.Bar
#   public import Foo.Bar
#   meta import Foo.Bar
#   public meta import Foo.Bar
#   @[...] import Foo.Bar
_IMPORT_RE = re.compile(
    r"^\s*(?:@\[.*?\]\s*)?(?:public\s+)?(?:meta\s+)?import\s+(\S+)",
    re.MULTILINE,
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

    modules = []
    for match in _IMPORT_RE.finditer(content):
        mod = match.group(1)
        # Sanity check: module names contain only alphanumeric, dots, underscores
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
