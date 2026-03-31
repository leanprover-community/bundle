#!/usr/bin/env bash
# Run all local tests against an existing bundle.
#
# Usage:
#   ./test-local.sh /path/to/bundle-root
#   BUNDLE_ROOT=/path/to/bundle-root ./test-local.sh
#
# The bundle root should contain: lean/, vscodium/, project/, Start Lean.sh
#
# Tests run:
#   - Python unit tests (no bundle needed)
#   - Tier 1: Bundle structure verification
#   - Tier 4: Launcher script tests
#   - Tier 6: Playwright GUI tests (infoview, diagnostics, project files)
set -euo pipefail

BUNDLE_ROOT="${1:-${BUNDLE_ROOT:-}}"

if [ -z "$BUNDLE_ROOT" ]; then
    # Try to find a bundle in common locations
    for candidate in /tmp/bundle-local/MDD154-bundle /tmp/bundle-fix*/MDD154-bundle /tmp/bundle-rebuild/MDD154-bundle; do
        if [ -f "$candidate/Start Lean.sh" ] 2>/dev/null || [ -f "$candidate/Start Lean.cmd" ] 2>/dev/null; then
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

cd "$(dirname "$0")"

echo ""
echo "=== Python unit tests ==="
python3 -m pytest tests/test_assemble.py tests/test_import_closure.py -v 2>&1 || true

echo ""
echo "=== Tier 1: Bundle structure ==="
python3 tests/verify_bundle.py "$BUNDLE_ROOT" --platform linux-x64

echo ""
echo "=== Tier 4: Launcher script tests ==="
python3 -m pytest tests/test_launcher.py -v 2>&1 || true

echo ""
echo "=== Tier 6: GUI tests ==="
cd tests/gui-playwright
if [ ! -d node_modules ]; then
    echo "Installing Playwright deps..."
    npm ci --silent
fi
./run-local.sh --reporter=list
