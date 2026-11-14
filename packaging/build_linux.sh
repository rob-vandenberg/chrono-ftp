#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# build_linux.sh 0.0.48
# --- Version history ----------------------------------------------------
# v0.0.48: Initial: the Linux program folder, built in the builder image, which is made once and reused
#
# Builds the Linux program folder of Chrono FTP (Python, Qt and the program in one folder; the user installs nothing
# else). It is built inside an Ubuntu 22.04 container: a program built on an older system also runs on newer ones, not
# the other way round. PySide6 6.12 needs glibc 2.34 itself, so 22.04 (glibc 2.35) is about the oldest that can work.
# The result is build/linux/chrono-ftp.dist/ in the project; packaging/package_linux.sh makes the .deb and .rpm from it.
# Needs podman. See packaging/PACKAGING.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/build/linux"

command -v podman >/dev/null || { echo "The build runs in a container and podman is not installed. Install it with: sudo apt install podman" >&2; exit 1; }

# The builder image (Ubuntu 22.04, its system packages, Python 3.14) is made once and kept by podman; it is made again
# only when its recipe changes (its name carries a checksum of Containerfile.builder and builder_setup.sh).
RECIPE="$(cat "$ROOT/packaging/Containerfile.builder" "$ROOT/packaging/builder_setup.sh" | sha256sum | cut -c1-12)"
IMAGE="localhost/chrono-ftp-builder:$RECIPE"
if ! podman image exists "$IMAGE"; then
    echo "Making the builder image once (it downloads Ubuntu's packages and Python 3.14; later builds reuse it)..."
    podman build --tag "$IMAGE" --file "$ROOT/packaging/Containerfile.builder" "$ROOT/packaging" \
        || { echo "The builder image could not be made; the lines above say why. Nothing is built." >&2; exit 1; }
fi

rm -rf "$OUT"
mkdir -p "$OUT"
podman run --rm \
    --volume "$ROOT:/src:ro,z" \
    --volume "$OUT:/out:z" \
    --volume chrono-ftp-build-cache:/cache \
    "$IMAGE" bash /src/packaging/build_in_container.sh \
    || { echo "The build in the container stopped; the lines above say why. The program folder was not finished, so no package can be made from it." >&2; exit 1; }
echo "Built: $OUT/chrono-ftp.dist"
