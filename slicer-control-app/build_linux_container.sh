#!/usr/bin/env bash
# ============================================================================
#  Build the Linux packages inside Ubuntu 22.04, so they run on 22.04 and
#  every later release (see packaging/linux/Containerfile for why).
#
#      ./build_linux_container.sh
#      VERSION=1.2.0 ./build_linux_container.sh
#
#  Needs podman (sudo apt install podman) or docker. Output lands in dist/,
#  exactly as with build_linux.sh.
#
# ============================================================================
set -euo pipefail
cd "$(dirname "$0")"

if command -v podman >/dev/null; then
    ENGINE=podman
    # Rootless podman maps container root to the calling user, so dist/ stays
    # owned by you.
    USERFLAG=()
elif command -v docker >/dev/null; then
    ENGINE=docker
    USERFLAG=(--user "$(id -u):$(id -g)" -e HOME=/tmp)
else
    echo "[ERROR] Neither podman nor docker found. Install one:"
    echo "        sudo apt install podman"
    exit 1
fi

IMAGE=piccola-cnc-build:jammy
"$ENGINE" build -t "$IMAGE" -f packaging/linux/Containerfile packaging/linux

# build-venv-jammy/ lives in the repo so pip's work survives between runs.
"$ENGINE" run --rm "${USERFLAG[@]}" \
    -v "$PWD:/src" \
    -e VERSION="${VERSION:-}" \
    -e BUILD_VENV=build-venv-jammy \
    "$IMAGE" ./build_linux.sh
