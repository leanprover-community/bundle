#!/usr/bin/env bash
# Run all local tests against an existing bundle.
#
# Usage:
#   ./test-local.sh /path/to/bundle-root
#   BUNDLE_ROOT=/path/to/bundle-root ./test-local.sh
#
# The bundle root should contain: lean/, vscodium/, project/, Start_Lean.sh
#
# Tests run:
#   - Python unit tests (no bundle needed)
#   - Tier 1: Bundle structure verification
#   - Tier 4: Launcher script tests
#   - Tier 6: Playwright GUI tests (infoview, diagnostics, project files)
set -euo pipefail
trap '' USR1 USR2

BUNDLE_ROOT="${1:-${BUNDLE_ROOT:-}}"

if [ -z "$BUNDLE_ROOT" ]; then
    for candidate in /tmp/bundle-local/*-bundle /tmp/bundle-fix*/*-bundle /tmp/bundle-rebuild/*-bundle; do
        if [ -f "$candidate/Start_Lean.sh" ] 2>/dev/null || [ -f "$candidate/Start_Lean.cmd" ] 2>/dev/null; then
            BUNDLE_ROOT="$candidate"
            break
        fi
    done
fi

if [ -z "$BUNDLE_ROOT" ] || [ ! -d "$BUNDLE_ROOT" ]; then
    echo "Usage: $0 /path/to/bundle-root"
    echo ""
    echo "Build a bundle first with:"
    echo "  python3 bundle.py https://github.com/PatrickMassot/MDD154 --platform linux-x64 --no-zip --work-dir /tmp/bundle-local"
    exit 1
fi

echo "Bundle: $BUNDLE_ROOT"
export BUNDLE_ROOT

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

echo ""
echo "=== Python unit tests ==="
python3 -m pytest tests/test_assemble.py tests/test_import_closure.py tests/test_git_shim.py -v || true

echo ""
echo "=== Tier 1: Bundle structure ==="
python3 tests/verify_bundle.py "$BUNDLE_ROOT" --platform linux-x64

echo ""
echo "=== Tier 4: Launcher script tests ==="
python3 -m pytest tests/test_launcher.py -v || true

echo ""
echo "=== Tier 6: GUI tests ==="
cd tests/gui-playwright
if [ ! -d node_modules ]; then
    echo "Installing Playwright deps..."
    npm ci --silent
fi

# Start Xvfb for headless GUI testing
XVFB_PID=""
cleanup() {
    pkill -f "codium" 2>/dev/null || true
    [ -n "$XVFB_PID" ] && kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT

XVFB=$(which Xvfb 2>/dev/null || find /nix/store -name Xvfb -type f 2>/dev/null | head -1)
if [ -z "$XVFB" ]; then
    echo "WARNING: Xvfb not found, skipping GUI tests"
    exit 0
fi

DISPLAY_NUM=$((RANDOM % 90 + 10))
rm -f "/tmp/.X${DISPLAY_NUM}-lock"
"$XVFB" ":${DISPLAY_NUM}" -screen 0 1920x1080x24 -nolisten tcp > /dev/null 2>&1 &
XVFB_PID=$!
sleep 2

if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "WARNING: Xvfb failed to start, skipping GUI tests"
    exit 0
fi

export DISPLAY=":${DISPLAY_NUM}"
npx playwright test --reporter=list
