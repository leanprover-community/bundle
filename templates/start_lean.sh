#!/bin/bash
# Launch VSCodium with Lean 4 for the bundled project.

BUNDLE_ROOT="$(cd "$(dirname "$0")" && pwd)"

# Add lean to PATH
export PATH="$BUNDLE_ROOT/lean/bin:$PATH"

# Prevent elan from interfering
export ELAN_HOME="$BUNDLE_ROOT/lean"

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
