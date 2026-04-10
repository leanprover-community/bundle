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

# Launch VSCodium
if [ -d "$BUNDLE_ROOT/vscodium/VSCodium.app" ]; then
    # macOS — strip quarantine attributes that block execution of binaries
    # extracted from a downloaded zip. Without this, Gatekeeper may silently
    # prevent lean/lake from running, causing the language server to fail.
    xattr -dr com.apple.quarantine "$BUNDLE_ROOT" 2>/dev/null || true

    # Invoke the binary directly so it inherits our environment.
    # `open` launches via Launch Services which drops env vars.
    exec "$BUNDLE_ROOT/vscodium/VSCodium.app/Contents/MacOS/VSCodium" "$BUNDLE_ROOT/project" "$@"
else
    # Linux
    exec "$BUNDLE_ROOT/vscodium/bin/codium" "$BUNDLE_ROOT/project" "$@"
fi
