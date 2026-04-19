#!/bin/bash
# Launch VSCodium with Lean 4 for the bundled project.

BUNDLE_ROOT="$(cd "$(dirname "$0")" && pwd)"

# Force VSCodium portable mode.  Without this, macOS looks for
# "codium-portable-data" next to VSCodium.app (the default name on macOS)
# and never finds our data/ folder, so extensions and settings silently
# don't load.  Setting VSCODE_PORTABLE short-circuits the platform-specific
# path logic and works uniformly on Linux, macOS, and Windows.
export VSCODE_PORTABLE="$BUNDLE_ROOT/vscodium/data"

# Strip any inherited elan from PATH and environment so the lean4 VS
# Code extension uses the bundled Lean directly.  If the student has
# ~/.elan/bin on PATH from a prior Lean course, the extension finds
# their elan, queries it about our toolchain, and (since they don't
# have our exact version installed) pops a modal "Lean version ... is
# not installed" dialog.  Removing elan from PATH — and *not* setting
# ELAN_HOME — makes the extension fall through to `lean` on PATH,
# which is our bundled one.
PATH="$(printf '%s\n' "$PATH" | tr ':' '\n' | grep -v '\.elan/' | grep -v '/elan/bin$' | paste -sd: -)"
unset ELAN_HOME

# Add lean to PATH
export PATH="$BUNDLE_ROOT/lean/bin:$PATH"

# Build LEAN_PATH from all package build directories
LEAN_PATH="$BUNDLE_ROOT/lean/lib/lean:$BUNDLE_ROOT/project/.lake/build/lib/lean"
for pkg_dir in "$BUNDLE_ROOT"/project/.lake/packages/*/; do
    if [ -d "$pkg_dir/.lake/build/lib/lean" ]; then
        LEAN_PATH="$LEAN_PATH:$pkg_dir/.lake/build/lib/lean"
    fi
done
export LEAN_PATH

# Determine which file to open (set during bundle assembly).
OPEN_FILE="@@OPEN_FILE@@"

# Build the argument list: workspace folder, then optional file.
ARGS=("$BUNDLE_ROOT/project")
if [ -n "$OPEN_FILE" ] && [ -f "$BUNDLE_ROOT/project/$OPEN_FILE" ]; then
    ARGS+=("$BUNDLE_ROOT/project/$OPEN_FILE")
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
