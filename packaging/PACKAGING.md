# PACKAGING.md: making the installers

*Version 0.0.48. Read this file completely before building. The project rules apply: change no code and release nothing
without the owner's "Proceed", and report every problem instead of working around it.*

## 1. What we make, and where

| File | Made by |
|---|---|
| `dist/chrono-ftp_<version>_amd64.deb` | `packaging/package_linux.sh` |
| `dist/chrono-ftp_<version>_x86_64.rpm` | `packaging/package_linux.sh` |

`<version>` is the version in `pyproject.toml`. Both are built from the same pushed version (the same git tag) and then
attached to the GitHub release of that version (section 5). Chrono FTP is a Linux program, so there is no Windows side.

The user downloads one file, installs it, and has a working program with a menu entry. Nothing else needs to be
installed: each package carries its own Python 3.14, Qt (PySide6 6.12) and Chrono FTP.

How: **Nuitka** compiles the program into a folder with everything it needs (`--mode=standalone`), and **nFPM** packs
that folder into the `.deb` and the `.rpm`.

## 2. Things that were learned the hard way (do not undo them)

1. **Nuitka is pinned to 4.2.2.** Nuitka 4.3 has a regression that corrupts compiled inner functions with PySide6 6.12
   and Python 3.14: the program crashes at startup, often but not always
   (https://github.com/Nuitka/Nuitka/issues/4081). Only move to a newer Nuitka after checking that this issue is fixed,
   and test the start several times.
2. **The password stores** are found by keyring at run time, which Nuitka cannot see. They are included explicitly:
   `--include-package=keyring.backends`, and `secretstorage` and `jeepney` for the desktop's own store. Without them a
   saved password cannot be read and every connection asks again (OR-24).
3. **The program's own package information.** The packaged program has no `pyproject.toml` beside it, so
   `src/chrono_ftp/core/appinfo.py` falls back to the package information for the version, the repository address and
   the licence text of the About window. Nuitka can compile that information in
   (`--include-distribution-metadata=chrono-ftp`) and then the version and the repository address are right but **the
   licence text is gone**, because the licence is read from the distribution's files and compiled information has no
   files (measured in the builder image, 2026-11-14). So the wheel's own `chrono_ftp-<version>.dist-info` folder is
   copied into the program folder instead (`--include-data-dir`), which gives all three. `build_in_container.sh` checks
   all three and refuses to go on when one is missing.
4. **Build on an old system.** A program built on a new Linux does not run on an older one. The build runs in an Ubuntu
   22.04 container (podman). PySide6 6.12 needs glibc 2.34 itself, so 22.04 (glibc 2.35) is about the oldest possible;
   the packages run on Ubuntu 22.04+, Debian 12+, Fedora 36+, Linux Mint 21+ and openSUSE with glibc 2.35+.
   **The old system brings old libraries, and the package always uses the copies it carries, never the system's.**
5. **Download once, never again.** Everything the build needs from outside is fetched once: Ubuntu's system packages and
   Python 3.14 go into the builder image (`packaging/Containerfile.builder` with `packaging/builder_setup.sh`), which
   `build_linux.sh` makes only when that recipe changed and podman keeps afterwards; the Python packages are kept as
   wheels in `/cache/wheels` (the podman volume `chrono-ftp-build-cache`) and fetched only when one is missing. Python
   3.14 is python-build-standalone's build (the Pythons of uv, made for old Linux systems, its SHA-256 checked), not
   Launchpad's deadsnakes archive, whose servers stopped answering in November 2026 and hung another of the owner's
   projects mid-build.
6. **The app information** (`packaging/chrono-ftp.metainfo.xml`, AppStream, installed in `/usr/share/metainfo`): without
   it Discover and GNOME Software do not list Chrono FTP under Installed, so it cannot be uninstalled there. It must
   name the package (`<pkgname>chrono-ftp</pkgname>`): Discover skips every program whose app information does not.
   Check it with `appstreamcli validate --no-net packaging/chrono-ftp.metainfo.xml`.
7. **No script inside the package installs anything.** The packages name `rsync` and the SSH client as dependencies and
   let `apt` or `dnf` install what is missing, where the user sees it. A post-install script that runs a package manager
   itself would break an offline install, and would do something as root that the user never saw.
   The deb name is `openssh-client`, the rpm name `openssh-clients`.
8. **The terminal and `xdg-open` stay out of the dependencies.** Chrono FTP opens the desktop's own terminal, and naming
   one would drag a foreign desktop's terminal onto the machine. Both are detected at run time and what is missing is
   greyed out (OR-7, D-016).
9. **Only the needed files go into the build container:** `pyproject.toml`, `README.md`, `LICENSE`, `src` and
   `packaging`. Never the whole project folder: it holds private files (OR-12).
10. **Test outside this machine.** A test on the machine that built the packages can pass for the wrong reason, because
    a Qt is installed there. `./build-release.sh` starts each package in a bare container as a normal user (Ubuntu 22.04 for the
    `.deb`, Fedora 43 for the `.rpm`); a program still running after eight seconds is a pass.

## 3. Building

Needs `podman` (Ubuntu package) and `nfpm` (https://github.com/goreleaser/nfpm/releases, the `_amd64.deb`; check its
checksum). Both are installed on the owner's machine.

```bash
packaging/package_linux.sh --build
```

That builds the program folder in the container and makes both packages in `dist/`. The first build makes the builder
image and fetches the wheels, which takes a while; later builds reuse both.

## 4. The whole way, in one command

```bash
./build-release.sh
```

It refuses unless the working tree is exactly on the pushed tag of the version in `pyproject.toml`, builds both
packages, starts each in a bare container, and stops there. Only the owner runs it (OR-30).

## 5. Putting it on the release page

Only for a version the owner installed and tried, and only when the owner says so (OR-29):

```bash
./build-release.sh --upload
```

or, when the packages are already in `dist/`:

```bash
packaging/upload_release.sh
```

It makes the release page from the tag when there is none yet and attaches both files, replacing a file of the same name.
`packaging/upload_release.sh` is private and not in git, like every other script that releases something (OR-12): it
exists only on the owner's machine.

## 6. Installing and removing by hand

```bash
sudo apt install ./dist/chrono-ftp_<version>_amd64.deb
```

```bash
sudo dnf install ./dist/chrono-ftp_<version>_x86_64.rpm
```

The program lives in `/opt/chrono-ftp`, is started by `/usr/bin/chrono-ftp` or from the menu, and is removed with
`sudo apt remove chrono-ftp` or `sudo dnf remove chrono-ftp`. The person's own settings in
`~/.config/chrono-ftp` are left alone, which is what a person expects when a program is removed and reinstalled.
