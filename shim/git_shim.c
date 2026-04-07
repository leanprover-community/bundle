/*
 * Tiny git.exe shim for the Lean 4 bundle.
 *
 * Purpose: the lean4 VS Code extension and VS Code's built-in `git` extension
 * both probe for `git` on PATH at startup. Without git they refuse to activate
 * (lean4) or log a missing-git warning and disable the SCM panel (built-in).
 * Historically the bundle shipped MinGit (~46 MB) to satisfy this probe. This
 * shim replaces MinGit with a <100 KB binary that answers exactly the probes
 * both extensions perform at activation and no more.
 *
 * Probe surface (verified against vscode-lean4 v0.0.229 source and
 * vscode/extensions/git source as of 2026-04):
 *
 *   lean4 extension (checkGitAvailable):
 *     git --version            -> must exit 0
 *
 *   VS Code built-in git extension (findGit + initial repository scan):
 *     git --version            -> must exit 0 AND print `git version X.Y.Z`
 *                                 where X.Y.Z parses as >= 2.0.0 and is NOT
 *                                 2.25.x or 2.26.x (both trigger modal
 *                                 deprecation warnings on Windows).
 *     git rev-parse --show-toplevel (per workspace folder and each direct
 *                                 subfolder) -> exit non-zero with stderr
 *                                 containing the phrase "Not a git repository"
 *                                 so the folder is silently ignored as
 *                                 non-git.
 *
 * Neither extension parses anything beyond exit code and version string, and
 * the lake build tool wraps its optional git probes (Reservoir cache, release
 * URL lookup) in `captureProc?` / `testProc` which ignore non-zero exits.
 * So the shim can safely reply to every unrecognized subcommand with the
 * "not a git repository" error shape.
 *
 * See issue #18 for the full discovery notes.
 *
 * Build:
 *   x86_64-w64-mingw32-gcc -O2 -s -o git.exe git_shim.c   (cross on Linux)
 *   gcc -O2 -s -o git.exe git_shim.c                      (native on Windows)
 */

#include <stdio.h>
#include <string.h>

/* Must be >= 2.0.0 and must NOT be 2.25.x/2.26.x (VS Code pops a modal
 * deprecation warning for those on Windows). Choosing 2.47.0 matches the
 * MinGit version previously shipped with the bundle. */
static const char VERSION_LINE[] = "git version 2.47.0\n";

/* VS Code's git extension matches stderr against /Not a git repository/i
 * (getGitErrorCode in extensions/git/src/git.ts). Any stderr containing
 * that phrase tags the error as NotAGitRepository, which is silently
 * swallowed by the model scanner. Real git exits 128 for this case. */
static const char NOT_A_REPO[] =
    "fatal: not a git repository (or any of the parent directories): .git\n";

static int is_version_flag(const char *a) {
    return strcmp(a, "--version") == 0
        || strcmp(a, "-v") == 0;
}

/* A few top-level flags consume the next argv entry (e.g. `git -C <dir>
 * rev-parse`). Skip over them when scanning for the subcommand so that a
 * caller like `git -c user.name=foo commit` doesn't confuse us into thinking
 * `user.name=foo` is the subcommand. */
static int consumes_next(const char *a) {
    return strcmp(a, "-C") == 0
        || strcmp(a, "-c") == 0
        || strcmp(a, "--git-dir") == 0
        || strcmp(a, "--work-tree") == 0
        || strcmp(a, "--namespace") == 0;
}

int main(int argc, char *argv[]) {
    const char *sub = NULL;
    int i = 1;

    while (i < argc) {
        const char *a = argv[i];
        if (a[0] != '-') {
            sub = a;
            break;
        }
        if (is_version_flag(a)) {
            fputs(VERSION_LINE, stdout);
            return 0;
        }
        if (consumes_next(a)) {
            i += 2;
            continue;
        }
        /* Unknown top-level flag: skip it and keep scanning. */
        i += 1;
    }

    if (sub != NULL && strcmp(sub, "version") == 0) {
        fputs(VERSION_LINE, stdout);
        return 0;
    }

    /* Any other invocation: pretend we're not inside a git repo. Both the
     * lean4 extension and the VS Code git extension either ignore this or
     * abort cleanly, and Lake's optional git probes (`captureProc?`,
     * `testProc`) treat non-zero exit as "no result" without surfacing the
     * error. The exit code (128) matches real git's "not a repository". */
    fputs(NOT_A_REPO, stderr);
    return 128;
}
