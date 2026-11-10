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

## 0.0.13 (2026-11-11): block FC, the queue

Queue control per item (D-046): pause and resume keeping what arrived, move up and down, run now, retry, stop and Stop All, delete, a speed limit per item that holds at once (rsync starts again with it), finished items that leave after a while, an error that goes on or pauses the queue, and `TransferQueue.close()` that waits for every thread and lets nothing reach the window afterwards. When the queue is done (D-047): stay, disconnect, close, suspend or shut down, once, after a countdown, held back by unsent edits. Several transfers at once (D-048): a scheduler thread and a thread per item, a login per FTP item, never two items on the same place, one question at a time with an answer for all carried over, the speed limit shared, and a server that takes no more logins making an item wait calmly; the fake SSH server of the tests now leaves no thread or descriptor behind. One large file in parts (D-049): up to four logins writing their own ranges into one part file, as one item, with the ranges noted for a resume.

Many review rounds over the three parts; every real fault came with a test that fails on the code before it, among them a job started twice while its previous run still ended, two items left waiting for good after logins refused at the same moment, a later item overtaking an earlier one on its place, an FTP login after a broken-off file that failed instead of waiting, and a split part that gave up after a few drops spread over a long file. Live tests passed on the real FTP test account (three transfers at once both ways, a download in parts) and on this computer's SSH server (a file in parts both ways).

Still open: the known limits of the transfers in `STATUS.md`; the live test on the SFTP test account without a shell has not run for this version.

## 0.0.14 (2026-11-10): the program icon

Chrono FTP has its own icon, made by the owner in the style of the Chrono programs: a blue square, a white folder with the letters FTP and the orange clock. The program ships it (`src/chrono_ftp/ui/resources/chrono-ftp.svg`, in the package data) and sets it for every window, the taskbar and Alt+Tab; the windows tell the desktop they belong to the start-menu entry `chrono-ftp`. A test checks that the icon draws its blue, white and orange.

Still open: the first installer must install the icon (`packaging/` does not exist yet).

## 0.0.15 (2026-11-10): the connections window keeps its size and place

The size and place of the connections window are kept in the window file with the main window's sizes, when the main window closes; a run that does not open it keeps the place it had. A saved place on a screen that is gone is brought back onto a screen by Qt. Two tests: after a restart, and after a run without the window.

## 0.0.16 (2026-11-10): a splitter in the connections window

The list of connections and the fields of the selected connection have a splitter between them; a wider window widens the fields. Its place is kept in the window file with the size and place of the window. A test: the splitter is where it was after a restart.

## 0.0.17 (2026-11-10): the connections window as the reference program

Import… and Export… (greyed until export exists, with a hover text) under the list; Connect, Duplicate and Delete in the right-click menu of a connection; New, Save and Connect under the fields. The row of five buttons under the fields is gone, so the fields side shrinks to about 300 pixels instead of 492. Duplicate saves a copy named "… (copy)" with its password, selected and ready to be renamed (D-050). Tests for Duplicate, the menu, the buttons under the list and the narrow fields.

## 0.0.18 (2026-11-10): the buttons of the connections window on one line

The fields side had its own top and bottom margin and the list side none, so its buttons stood higher than Import… and Export…. Both sides now start and end at the same height; a test checks that all five buttons stand on one line.

## 0.0.19 (2026-11-10): Import… and Export… side by side

Export… stands right next to Import… on the left under the list of the connections window, instead of at the right edge; a test checks it.

## 0.0.20 (2026-11-10): room between the splitter and the labels

0.0.18 meant to keep the left margin of the fields side, but read it before the layout was on its widget, where it is 0, so the labels touched the splitter. The margin is now the style's own; top and bottom stay 0, so the buttons stay on one line. A test checks the room.

## 0.0.21 (2026-11-10): an eye in the password fields

The password fields of the connections window and the login window have an eye (the Breeze icons view-visible and view-hidden) that shows or hides what was typed. It appears only while the field holds typed text; an emptied field, also when another connection is chosen, hides the password again. A stored password is never in the field, so it can never be shown. Tests in `tests/ui/test_ui_passwordeye.py`.

## 0.0.22 (2026-11-10): no hole in front of New Tab

An empty tab bar kept 123 pixels free for its scroll arrows, a hole in front of New Tab. The tab bar now shows only while it holds a tab; the row keeps the height of a tab, so nothing below it jumps. A test checks both.

## 0.0.23 (2026-11-10): bigger icons in the side bars

The icons of the navigation and action bars of both sides are 24 pixels instead of 18. The icons are now also drawn at 24 and 48 pixels, so they stay sharp. The general bar and the queue bar stay at 18. A test checks the size.
