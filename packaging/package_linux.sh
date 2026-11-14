#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# package_linux.sh 0.0.48
# --- Version history ----------------------------------------------------
# v0.0.48: Initial: the .deb and .rpm from the program folder
#
# Makes dist/chrono-ftp_<version>_amd64.deb and dist/chrono-ftp_<version>_x86_64.rpm from the program folder that
# packaging/build_linux.sh built (it runs that first when the folder is missing or --build is given). Needs nfpm.
# The version is the one in pyproject.toml. See packaging/PACKAGING.md.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml 2>/dev/null | head -1 || true)"
export VERSION
[[ -n "$VERSION" ]] || { echo "No line of the form version = \"x.y.z\" was found in $ROOT/pyproject.toml, so the packages would have no version. Nothing is made." >&2; exit 1; }
command -v nfpm >/dev/null || { echo "nfpm makes the packages and is not installed. Get its _amd64.deb from https://github.com/goreleaser/nfpm/releases, check its checksum, and install it with: sudo apt install ./nfpm_<version>_amd64.deb" >&2; exit 1; }

# The program folder is built again unless the one lying here is this version's: a folder left by an earlier version
# would otherwise be packed under the new version's name, and the About window would show the old one.
if [[ "${1:-}" == "--build" || ! -x build/linux/chrono-ftp.dist/chrono-ftp \
      || ! -d "build/linux/chrono-ftp.dist/chrono_ftp-${VERSION}.dist-info" ]]; then
    packaging/build_linux.sh
fi

mkdir -p dist
nfpm package --config packaging/nfpm.yaml --packager deb --target "dist/chrono-ftp_${VERSION}_amd64.deb"
nfpm package --config packaging/nfpm.yaml --packager rpm --target "dist/chrono-ftp_${VERSION}_x86_64.rpm"
ls -lh dist/chrono-ftp_"${VERSION}"_*
