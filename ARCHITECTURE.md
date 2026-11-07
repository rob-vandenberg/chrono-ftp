# ARCHITECTURE.md

The design as it stands now (version 0.0.1). The rules behind it, with their reasons, are in `DECISIONS.md` (the D-numbers below). No package holds program code yet: the packages exist so that the layers are in place from the start.

## 1. Goals that shape the design

1. **The person always knows where they are.** Left is local, right is remote, a colour means a live connection (D-006, D-007).
2. **Work on a server happens on the server** (D-012, D-015).
3. **The window never freezes,** whatever a slow connection does (D-019).
4. **Testable without a screen.** Every action is an engine command (D-022).

## 2. Package layout

All code lives in the package `chrono_ftp` (src layout: `src/chrono_ftp/`).

| Module | Responsibility |
|---|---|
| `core` | Pure data model: Connection, remote file entry, capabilities, error types, colours (`core/colours.py`), the thin door to the credential store. No other I/O. |
| `config` | The connection list (`~/.config/chrono-ftp/connections.json`), settings, the one-time import of a session list. |
| `connections` | The common connection layer and its two implementations, SSH and FTP; capability detection. |
| `transfers` | The transfer queue: `rsync` with a speed limit, falling back to SFTP or FTP; progress. |
| `engine` | The command registry. Every user action is a command here. |
| `ui` | PySide6 user interface. Thin: calls engine commands only. |

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

1. **SSH** (D-010): browse over SFTP and run shell commands over the same login, with `asyncssh`. What a server without a shell does is an open question (`STATUS.md`).
2. **FTP** (D-011): `ftplib`, with TLS where the server offers it, `MLSD` for listings.

Each connection reports what it can do after connecting (D-016): the system, the tools and their options, what the account may do. What is unavailable is greyed out.

## 5. Actions on a server

1. Recursive owner, group and permissions: one `chown -R` or `chmod -R` on the server, after the exact command is shown and confirmed (D-012, D-013).
2. Rename, move, copy, delete and search: `mv`, `cp -a`, `find` and the like on the server (D-015).
3. Every path in a shell command goes through `shlex.quote`.

## 6. The window

1. Left: the local side, neutral, with `QFileSystemModel`. Right: the remote side with a custom model that loads in the background (D-019).
2. Both the folder tree and the file list on the right take the connection's colour; text is black or white by the background's brightness, selected rows too (D-007).
3. Every tab is one connection and keeps its own local and remote folder: a click on another tab changes both sides (D-008).
4. Tabs have the connection's colour, inactive tabs slightly dimmed, through a small custom tab bar.
