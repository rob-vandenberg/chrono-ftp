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

## 0.0.10 (2026-11-07): FTP listings without OPTS MLST

The listing asked the server for a list of details first, which a real server refused with 500, and that was read as having no MLSD at all. A refusal of that request is now ignored: the listing goes on, and a server that has the option still sends rights and owner.

## 0.0.11 (2026-11-07): block FA, the main window in parts

`ui/mainwindow.py` went from 1,285 lines to about 270: every group of actions moved, unchanged, into a module of its own (D-041); a syntax-tree comparison confirmed that all 115 methods, functions, classes and numbers are the same. The unstable test of the sessions file had two causes: four tests ended when the file arrived, before its transfer had ended, so closing the window asked a question nobody answered; and a thread could still answer a deleted window. The tests now wait for the end of their transfers, and a closed window takes no more answers (D-042). Twenty runs of the sessions file in a row passed without a warning.

## 0.0.12 (2026-11-10): block FB, transfer options

The transfer window before every upload, download and drop, with Transfer settings for one transfer and the defaults in Settings (D-043); resume from part files with notes, kept and tidied by rule, with names of our own within 255 bytes (D-044); the overwrite question, whose answers hold only for the file as the person saw it, on both ways (D-045); dates, rights, only new and updated, count first and move, greyed where a server can't do them. rsync is preceded by a look-over wherever a file is asked about, runs in a process group of its own, and is never fed a file name as progress. Pipes, sockets, devices and links to folders are never sent, fetched or replaced as files, and are told, never left out in silence.

Twenty-one review rounds: the first twenty found real faults, among them a rename onto a link that let rsync replace the kept file, a fallback after rsync could not log in that replaced a file changed after the question, a last-moment save that the replace removed, part files lost or left without a note, a picked item gone from the server that made an empty folder here, and test children and server threads that outlived their tests. Each fault came with a test that fails on the code before it; surviving mutants of the review rounds are killed by tests, the few that remain are equivalent. Round 21 found nothing real; its two untrue texts (a link to a place that can't be opened, and a server that refuses to tell about a file) were fixed.

Still open: the known limits of the transfers in `STATUS.md`; `TransferQueue.close()` does not wait for its thread (block FC); the live test on the SFTP test account without a shell has not run for this version.
