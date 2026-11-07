# ARCHITECTURE.md

The design as it stands now (version 0.0.5). The rules behind it, with their reasons, are in `DECISIONS.md` (the D-numbers below). 

## 1. Goals that shape the design

1. **The person always knows where they are.** Left is local, right is remote, a colour means a live connection (D-006, D-007).
2. **Work on a server happens on the server** (D-012, D-015).
3. **The window never freezes,** whatever a slow connection does (D-019).
4. **Server work is testable without a screen** (D-022).

## 2. Package layout

All code lives in the package `chrono_ftp` (src layout: `src/chrono_ftp/`).

| Module | Responsibility |
|---|---|
| `core` | Pure data model: Connection, remote file entry, capabilities, error types, colours (`core/colours.py`), the thin door to the credential store. No other I/O. |
| `config` | The connection list (`~/.config/chrono-ftp/connections.json`), the one-time import of a session list. |
| `connections` | The common connection layer (`base.py`), its two implementations (`ssh.py`, `ftp.py`), capability detection, and the servers the person trusted (`trust.py`). |
| `transfers` | The transfer queue: `rsync` with a speed limit, falling back to SFTP or FTP; progress. |
| `engine` | Between the window and the rest: a thread per connection (`jobs.py`), the commands that run on a server (`commands.py`), the terminal (`terminal.py`). |
| `ui` | PySide6 user interface. Pure window actions live here; server work does not. |

There is no separate command-line program (D-022).

## 3. Layers

A module may import only from lower layers. Modules in one layer are independent siblings and do not import each other. Enforced by import-linter (`.venv/bin/lint-imports`, contract in `pyproject.toml`).

| Layer | Modules |
|---|---|
| L0 | `core` |
| L1 | `config`, `connections` |
| L2 | `transfers` |
| L3 | `engine` |
| L4 | `ui` |

## 4. The connection layer

The panes, tabs and colours do not know which connection type is in use. SSH and FTP are equals, and each server uses whichever it supports (D-009).

1. **SSH** (D-010): browse over SFTP and run shell commands over the same login, with `asyncssh`. Without a shell the actions that need one are greyed out (D-027).
2. **FTP** (D-011): `ftplib`, with TLS where the server offers it, `MLSD` for listings.

Each connection reports what it can do after connecting (D-016): the system, the tools and their options, what the account may do. What is unavailable is greyed out; on an FTP connection the actions that need a shell are not shown (D-027).

## 5. Actions on a server

1. Recursive owner, group and permissions: one `chown -R` or `chmod -R` on the server, after the exact command is shown and confirmed (D-012, D-013).
2. Rename, move, copy, delete and search: `mv`, `cp -a`, `find` and the like on the server (D-015).
3. Without a shell: rename, move and delete through the protocol, which is slower; copy, search, the recursive changes, the terminal and the `sha256sum` step of compare are not available (D-027). Comparing by size and date works on every connection (D-020).
4. Every path in a shell command goes through `shlex.quote`.

## 6. The window

1. Left: the local side, neutral, with `QFileSystemModel`: folder tree and file list next to each other (`ui/localside.py`). Right: the remote side (`ui/remoteside.py`), filled by answers from the connection's own thread (D-019). The look is set in `ui/theme.py`: Fusion with the colours of `core/colours.py`, so nothing comes from the platform (OR-9).
2. Both the folder tree and the file list on the right take the connection's colour; text is black or white by the background's brightness, selected rows too (D-007).
3. Every tab is one connection and keeps its own local and remote folder: a click on another tab changes both sides (D-008).
4. Tabs have the connection's colour, inactive tabs slightly dimmed, a dropped connection grey, through a small custom tab bar above both sides (D-007, D-008).
5. The layout follows the reference program; the buttons use Breeze icons from `ui/resources/icons/`, loaded by `ui/icons.py`: the text colour of the icon follows the theme, its other colours stay, and a disabled icon is one grey shape (D-028).
6. A local folder that can't be opened is shown as closed: an empty list and "no access" on the path line. The side watches its folder and every folder above it, so a removal or rename moves it up to the nearest folder that is left.
