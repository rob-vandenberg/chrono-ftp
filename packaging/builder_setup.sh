#!/usr/bin/env bash
# --- Version ------------------------------------------------------------
# builder_setup.sh 0.0.48
# --- Version history ----------------------------------------------------
# v0.0.48: Initial: everything a build needs from outside, done once when the builder image is made (Ubuntu's system
#          packages and Python 3.14). Nothing about SQLite: Chrono FTP keeps no database.
#
# Runs once, inside packaging/Containerfile.builder, when podman makes the builder image (packaging/build_linux.sh makes
# it when its recipe changed). Everything it downloads stays in the image, so a build downloads none of it again.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

# A compiler for Nuitka, and the system libraries Qt loads, so that Nuitka can follow their dependencies. Chrono FTP uses
# QtCore, QtGui, QtWidgets, QtSvg, QtNetwork and QtDBus: no HTML viewer, so none of QtWebEngine's libraries are here.
# Python 3.14 (the program's minimum) is a ready-made build of python-build-standalone (the Pythons of uv, built for old
# Linux systems), downloaded once into the image with its SHA-256 checked. Ubuntu's own packages come from its archive
# (a slow download is tried again).
printf 'Acquire::Retries "5";\nAcquire::http::Timeout "60";\nAcquire::https::Timeout "60";\n' > /etc/apt/apt.conf.d/80retries
apt-get update -qq
apt-get install -y -qq ca-certificates curl gcc g++ patchelf ccache \
    libgl1 libegl1 libxkbcommon0 libxkbcommon-x11-0 libfontconfig1 libdbus-1-3 libxi6 libxrandr2 libxtst6 libxfixes3 \
    libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 libxcb-shape0 libxcb-xinerama0 libxcb-image0 libxcb-render-util0 \
    libx11-xcb1 libwayland-client0 libwayland-cursor0 libwayland-egl1 \
    libglib2.0-0 libgssapi-krb5-2 libexpat1 libbz2-1.0 libzstd1 libbrotli1 libfreetype6 libharfbuzz0b >/dev/null

PYTHON_URL="https://github.com/astral-sh/python-build-standalone/releases/download/20261110/cpython-3.14.10%2B20261110-x86_64-unknown-linux-gnu-install_only.tar.gz"
PYTHON_SHA256="3fe0d7c0538eb5d190c34582992d26230532551355cbd789fabc6591621cfc07"
PYTHON_ARCHIVE="/tmp/cpython-3.14.10-20261110.tar.gz"
curl -fsSL --retry 5 -o "$PYTHON_ARCHIVE" "$PYTHON_URL" || { echo "Python 3.14 could not be downloaded from $PYTHON_URL. Check that this computer is online and make the builder image again."; exit 1; }
echo "$PYTHON_SHA256  $PYTHON_ARCHIVE" | sha256sum -c --quiet || { echo "The Python 3.14 that was downloaded does not match the checksum published with it, so it is not used and the builder image is not made. Try again; report it when it happens a second time."; exit 1; }
tar -C /opt -xzf "$PYTHON_ARCHIVE"
rm "$PYTHON_ARCHIVE"
ln -sf /opt/python/bin/python3.14 /usr/local/bin/python3.14

rm -rf /var/lib/apt/lists/*
