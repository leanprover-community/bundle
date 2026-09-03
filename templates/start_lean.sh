#!/bin/bash
# Launch VSCodium with Lean 4 for the bundled project.

BUNDLE_ROOT="$(cd "$(dirname "$0")" && pwd)"

# Force VSCodium portable mode.  Without this, macOS looks for
# "codium-portable-data" next to VSCodium.app (the default name on macOS)
# and never finds our data/ folder, so extensions and settings silently
# don't load.  Setting VSCODE_PORTABLE short-circuits the platform-specific
# path logic and works uniformly on Linux, macOS, and Windows.
export VSCODE_PORTABLE="$BUNDLE_ROOT/vscodium/data"

# If the student has a pre-existing elan install, the lean4 VS Code
# extension unconditionally prepends $HOME/.elan/bin to PATH on
# activation and queries that elan about the project's toolchain.
# When elan doesn't have our exact toolchain installed, it pops a
# modal "Lean version ... is not installed" dialog.
#
# Fix: symlink the bundled Lean into the student's elan toolchains
# directory.  Elan then reports the toolchain as installed and the
# extension proceeds normally.  The symlink reuses our binaries, so
# no disk duplication.
#
# (We also strip elan from PATH and clear ELAN_HOME defensively for
# students with no elan installed at all: if ~/.elan doesn't exist,
# the extension's PATH prepend is a no-op and it falls through to
# `lean` on PATH, which is our bundled one.)
PATH="$(printf '%s\n' "$PATH" | tr ':' '\n' | grep -v '\.elan/' | grep -v '/elan/bin$' | paste -sd: -)"
unset ELAN_HOME

# Add lean to PATH
export PATH="$BUNDLE_ROOT/lean/bin:$PATH"

# Register the bundled toolchain with the student's elan (no-op if
# they don't have elan).  Bypasses `elan toolchain link` — which
# refuses release-format names like leanprover/lean4:v4.26.0 — by
# creating the directory elan expects to find directly.
# The encoded name is substituted at bundle assembly time.
_TC_ENC="@@TOOLCHAIN_ENCODED@@"
if [ -n "$_TC_ENC" ] && [ -x "$HOME/.elan/bin/elan" ] && [ ! -e "$HOME/.elan/toolchains/$_TC_ENC" ]; then
    mkdir -p "$HOME/.elan/toolchains"
    ln -s "$BUNDLE_ROOT/lean" "$HOME/.elan/toolchains/$_TC_ENC" 2>/dev/null || true
fi
unset _TC_ENC

# Build LEAN_PATH from all package build directories
LEAN_PATH="$BUNDLE_ROOT/lean/lib/lean:$BUNDLE_ROOT/project/.lake/build/lib/lean"
for pkg_dir in "$BUNDLE_ROOT"/project/.lake/packages/*/; do
    if [ -d "$pkg_dir/.lake/build/lib/lean" ]; then
        LEAN_PATH="$LEAN_PATH:$pkg_dir/.lake/build/lib/lean"
    fi
done
export LEAN_PATH

# Open the configured file only on the first launch of this extracted
# bundle. Later launches let VSCodium restore the student's own open tabs.
OPEN_FILE="@@OPEN_FILE@@"
OPEN_MARKER="$VSCODE_PORTABLE/user-data/User/.lean-bundle-default-opened"

# Build the argument list: workspace folder, then optional first-launch file.
ARGS=("$BUNDLE_ROOT/project")
if [ ! -e "$OPEN_MARKER" ] && [ -n "$OPEN_FILE" ] && [ -f "$BUNDLE_ROOT/project/$OPEN_FILE" ]; then
    ARGS+=("$BUNDLE_ROOT/project/$OPEN_FILE")
    : > "$OPEN_MARKER"
fi

# Launch VSCodium
if [ -d "$BUNDLE_ROOT/vscodium/VSCodium.app" ]; then
    # macOS — strip quarantine attributes that block execution of binaries
    # extracted from a downloaded zip. Without this, Gatekeeper may silently
    # prevent lean/lake from running, causing the language server to fail.
    xattr -dr com.apple.quarantine "$BUNDLE_ROOT" 2>/dev/null || true

    echo "=========================================="
    echo "  Launching Lean 4 editor..."
    echo "  You can close this terminal window."
    echo "=========================================="

    # Launch VSCodium in the background and detach it from this terminal
    # so that closing the terminal window won't kill the editor.
    # We invoke the binary directly (not `open`) so it inherits our
    # environment — `open` launches via Launch Services which drops env vars.
    "$BUNDLE_ROOT/vscodium/VSCodium.app/Contents/MacOS/VSCodium" "${ARGS[@]}" "$@" &
    disown
    exit 0
else
    # Linux — no terminal window issue, so exec is fine.
    exec "$BUNDLE_ROOT/vscodium/bin/codium" "${ARGS[@]}" "$@"
fi
