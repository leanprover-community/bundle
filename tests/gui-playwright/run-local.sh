#!/usr/bin/env bash
# Run Tier 6 Playwright tests locally with Xvfb.
# Usage: BUNDLE_ROOT=/path/to/bundle ./run-local.sh
set -u
trap '' USR1 USR2

: "${BUNDLE_ROOT:?BUNDLE_ROOT must be set}"

XVFB_PID=""
cleanup() {
    pkill -f "codium" 2>/dev/null || true
    [ -n "$XVFB_PID" ] && kill "$XVFB_PID" 2>/dev/null || true
}
trap cleanup EXIT

# Find Xvfb
XVFB=$(which Xvfb 2>/dev/null || find /nix/store -name Xvfb -type f 2>/dev/null | head -1)
if [ -z "$XVFB" ]; then
    echo "ERROR: Xvfb not found"
    exit 1
fi

# Start Xvfb on a random display
DISPLAY_NUM=$((RANDOM % 90 + 10))
rm -f "/tmp/.X${DISPLAY_NUM}-lock"
"$XVFB" ":${DISPLAY_NUM}" -screen 0 1920x1080x24 -nolisten tcp > /dev/null 2>&1 &
XVFB_PID=$!
sleep 2

if ! kill -0 "$XVFB_PID" 2>/dev/null; then
    echo "ERROR: Xvfb failed to start"
    exit 1
fi
echo "Xvfb running on :${DISPLAY_NUM} (PID $XVFB_PID)"

export DISPLAY=":${DISPLAY_NUM}"

cd "$(dirname "$0")"
npx playwright test "$@" 2>&1
exit $?
