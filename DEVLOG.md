# DEVLOG.md

Append-only log, one entry per completed task, newest last.

## 0.0.1 (2026-11-07): project start

The repository, the documents, the rules, the layers, the private push tools and the first tests. No program function yet.

## 0.0.2 (2026-11-07): the owner's answers

Actions that need a shell (D-027), the grey tab of a dropped connection, the tab bar above both sides, Breeze icons (D-028), and the license AGPL-3.0 only. Still open: the three questions in `STATUS.md`.

## 0.0.3 (2026-11-07): the main window

The window test plugin, `core/colours.py`, the main window with the tab bar above both sides, the local side with folder tree and file list, the grey right side. Comparing by size and date works on every connection; only `sha256sum` needs a shell (D-020, D-027).

## 0.0.4 (2026-11-07): Breeze icons, and OR-16 removed

The icons of D-028 with their licence file and `ui/icons.py`; no button uses them yet. OR-16 is gone: server work stays out of the window, pure window actions stay in it (D-022).

## 0.0.5 (2026-11-07): the first working version

Milestones 1, 2 and 3 in one go, on the owner's word to continue without stopping. Measured on this computer with a throwaway tree: chmod over 5,000 files in 0.04 s; rsync 276 MB/s; SFTP 121 MB/s before the switch to asyncssh's pipelined copy.
