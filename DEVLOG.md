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

## 0.0.6 (2026-11-07): daily use

One shared file list on both sides, with buttons per side after the owner's picture of the goal, drag and drop, Edit, Synchronize, local actions, tidying of leftovers, and names that are not valid UTF-8. The real window ran against a real test account over the network: every step passed at about 100 MB/s. A real SSH server on this computer confirmed that a link deep in a target tree stays a link and that a name that is not valid UTF-8 can be listed, fetched and deleted.

## 0.0.7 (2026-11-07): height for the file lists

One top row and one button row per side; the queue under a separator, hidden when idle unless pinned, and open after a failure. In a window 800 pixels high the lists went from about 410 to 661 pixels with the queue hidden.

## 0.0.8 (2026-11-07): the login window

User and password in one window; a connection without a user asks before its first try; a typed user is kept once the server accepted it.

## 0.0.9 (2026-11-07): the colour is optional

A new connection has no colour; a tick in the Connections window switches it on. A live connection without a colour looks like the left side, with a neutral tab and the normal terminal colours; not connected stays plain grey.
