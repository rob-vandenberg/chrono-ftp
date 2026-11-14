#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# build_in_container.sh 0.0.48
# --- Version history ----------------------------------------------------
# v0.0.48: Initial: the Linux program folder, built inside the builder image; the Python packages come from the wheel
#          folder /cache/wheels and are fetched only when one is missing, so a build downloads nothing
#
# Runs inside the builder image started by build_linux.sh: /src is the project (read only), /out receives the program
# folder, /cache keeps what was fetched once between builds (the wheels and Nuitka's own cache).
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
export PIP_CACHE_DIR=/cache/pip NUITKA_CACHE_DIR=/cache/nuitka

# Only what the build needs is copied, so that building does not write into the read-only source. Never the whole
# project folder: it holds private files (OR-12).
mkdir -p /work
tar -C /src --exclude='*/__pycache__' --exclude='*.egg-info' -cf - pyproject.toml README.md LICENSE src packaging | tar -C /work -xf -
cd /work
VERSION="$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)"
[[ -n "$VERSION" ]] || { echo "No line of the form version = \"x.y.z\" was found in the project's pyproject.toml; nothing is built."; exit 1; }

python3.14 -m venv /work/venv
# Nuitka 4.3 corrupts compiled inner functions with PySide6 and Python 3.14 (startup crash, Nuitka issue 4081); 4.2.2 is
# the last good version. Move on to a newer one only after checking that the issue is fixed, and test the start several times.
# The Python packages come from the wheel folder kept in /cache/wheels, without asking PyPI. Only when one is missing (the
# first build, or a new or changed dependency) is it fetched once and kept; the program's own wheel never stays there.
WANTED=(nuitka==4.2.2 ordered-set zstandard)
mkdir -p /cache/wheels
missing_packages() {
    echo "The Python packages Chrono FTP is built with could not be fetched or built (asyncssh, cryptography, keyring, PySide6"
    echo "and Nuitka), so nothing is built. pip's own lines above say why; the usual cause is a computer that is offline."
    exit 1
}
if ! /work/venv/bin/pip install -q --no-index --find-links /cache/wheels /work "${WANTED[@]}" 2>/dev/null; then
    /work/venv/bin/pip download -q --dest /cache/wheels "setuptools>=80" || missing_packages  # what building the program's own package needs (pyproject.toml)
    /work/venv/bin/pip wheel -q --wheel-dir /cache/wheels /work "${WANTED[@]}" || missing_packages
    rm -f /cache/wheels/chrono_ftp-*.whl
    /work/venv/bin/pip install -q --no-index --find-links /cache/wheels /work "${WANTED[@]}" || missing_packages
fi

# keyring finds its password stores at run time, which Nuitka cannot see: they are named here, with the Linux ones
# (secretstorage and jeepney, the way to the desktop's own password store).
# The program's own package information is the wheel's own folder, copied into the program folder as it is. Nuitka can
# also compile that information in (--include-distribution-metadata=chrono-ftp), and then the version and the repository
# address are right but the licence text is gone, because core/appinfo.py reads the licence from the files of the
# distribution and compiled information has no files (measured in this container, 2026-11-14). The folder gives all three.
DIST_INFO_SOURCE="$(ls -d /work/venv/lib/python3.14/site-packages/chrono_ftp-*.dist-info 2>/dev/null || true)"
[[ -d "$DIST_INFO_SOURCE" ]] || { echo "The program's own package information (chrono_ftp-*.dist-info) is not in the build's Python folder, so Chrono FTP itself did not install there; nothing is built."; exit 1; }
/work/venv/bin/python -m nuitka \
    --mode=standalone \
    --assume-yes-for-downloads \
    --enable-plugin=pyside6 \
    --include-qt-plugins=sensible \
    --include-package=chrono_ftp \
    --include-package-data=chrono_ftp \
    --include-package=keyring.backends \
    --include-package=secretstorage \
    --include-package=jeepney \
    --include-distribution-metadata=keyring \
    --include-data-dir="$DIST_INFO_SOURCE=chrono_ftp-${VERSION}.dist-info" \
    --output-dir=/out \
    --output-filename=chrono-ftp \
    packaging/chrono_ftp_main.py
# The About window reads the version, the repository address and the licence from that package information
# (core/appinfo.py). All three are checked here, so a build that would show "unknown" or no licence never becomes a package.
DIST_INFO="/out/chrono_ftp_main.dist/chrono_ftp-${VERSION}.dist-info"
HERE="build/linux/chrono_ftp_main.dist/chrono_ftp-${VERSION}.dist-info"  # the same folder as the project sees it, outside the container
TRY_AGAIN="Build again with packaging/package_linux.sh --build; report it when it happens a second time."
[[ -f "$DIST_INFO/METADATA" ]] || { echo "The program folder carries no package information at $HERE, so the About window would say the version is unknown. $TRY_AGAIN"; exit 1; }
grep -Fqx "Version: ${VERSION}" "$DIST_INFO/METADATA" || { echo "The package information in $HERE is not for version $VERSION, so the About window would show another version. $TRY_AGAIN"; exit 1; }
grep -q "^Project-URL: Repository" "$DIST_INFO/METADATA" || { echo "The package information in $HERE carries no repository address, which the About window shows. $TRY_AGAIN"; exit 1; }
[[ -s "$DIST_INFO/licenses/LICENSE" || -s "$DIST_INFO/LICENSE" ]] || { echo "The package information in $HERE carries no licence file, so the About window could not show the licence. $TRY_AGAIN"; exit 1; }
# Only now, with every check passed, does the folder take the name the packages are made from, so a folder that was
# refused is never packed by a later packaging/package_linux.sh. Nuitka names it after the script.
rm -rf /out/chrono-ftp.dist
mv /out/chrono_ftp_main.dist /out/chrono-ftp.dist
echo "Built: /out/chrono-ftp.dist (version $VERSION)"
