# ROADMAP.md

The milestones and tasks of Chrono FTP. Status: done, next, open, postponed.

## Milestone 0: project start

1. Repository, documents, rules, layers and first tests: done (0.0.1).

## Milestone 1: browse with colours

1. Main window with the fixed left and right side: done (0.0.3).
2. Copy the Breeze icons of D-028, with their licence file: done (0.0.4).
3. Connection list with colours, connection dialogs: done (0.0.5).
4. Common connection layer, SSH connection, capability detection: done (0.0.5).
5. Tabs in the connection's colour, each with its own local and remote folder: done (0.0.5).
6. FTP connection: done (0.0.5).

Result: connect to and browse a real machine, with the colours working.

## Milestone 2: everyday copying

1. Speed test of `rsync` over SSH with a large file, against FTP: partly done. On this computer `rsync` and SFTP were measured; the test against a real FTP server waits for a login on it.
2. Transfer queue with progress and throttling: done (0.0.5).
3. Remote rename, move, copy and delete: done (0.0.5).

Result: replaces the reference program for everyday copying.

## Milestone 3: the rest of the scope

1. Recursive owner, group and permissions, measured on a throwaway folder with a few thousand files: done (0.0.5); 5,000 files took under a tenth of a second on this computer.
2. Search on the remote: done (0.0.5).
3. Terminal launch: done (0.0.5).
4. One-time import of a session list: done (0.0.5), checked by the owner with a real export.
5. The folder `packaging/` for the first installer: open.
6. Compare local and remote files: done (0.0.6), as Synchronize.

## Milestone 4: daily use

1. Test of the real window against a real test account: done (0.0.6).
2. Drag and drop between the sides and from a file manager: done (0.0.6).
3. Sorting, keys, right-click menus, remembered sizes: done (0.0.6).
4. Buttons per side, path line, count line, rights, link target, filter, status bar: done (0.0.6).
5. Edit of a server file in the person's own program: done (0.0.6).
6. Rename, delete and new folder on this computer: done (0.0.6).
7. Leftovers of a dropped transfer tidied; links in a target tree kept; names that are not valid UTF-8: done (0.0.6).
8. The connection colour in the terminal: done (0.0.6).
9. The most height for the file lists: one top row, one button row per side, the queue under a separator, hidden when idle unless pinned: done (0.0.7).
10. One login window with user and password: done (0.0.8).
11. The colour of a connection is optional, off for a new one: done (0.0.9).

## Milestones 5 to 21: the full counterpart

One milestone per block of the private `FEATURES.md` (D-001, OR-13), in the order of [[D-054]]. The size of each block is an estimate (small, medium, large, huge), not a measurement.

5. FA, the main window split into modules: done in 0.0.11.
6. FB, transfer options: done in 0.0.12.
7. FC, the queue: done in 0.0.13.
8. FH, navigation, medium: done in 0.0.34. A clickable path, bookmarks, location profiles, the same folder on the other side, synchronized browsing, and remembered starting folders (D-055 to D-058).
9. FM, Properties and server actions, large: done in 0.0.37. Properties on both sides and for several files, a rights editor, owner and group lists, links, Duplicate and Copy To on the server, a new file, a new folder with rights, installing your key, changing the server password, the file manager, and the terminal in the folder shown (D-060 to D-063).
10. FP, program-wide, medium: done in 0.0.38. Preferences with pages and confirmations, a session log, a server information window with free space, cleaning up stored data, the About window (0.0.35, D-059) with its licence, and an update check on request (D-064 to D-068).
11. FQ, looks, small: next. File colours by mask, thumbnails, and a customisable toolbar.
12. FG, selection and keys, large: open. Type-ahead, selection keys, a keyboard-driven two-pane mode, the full key set, size format, column menu, natural sort, path in the title, clipboard copies, and selection totals.
13. FD, masks, large: open. One filter engine for include and exclude by name, path, size and date; text mode with line-ending conversion; file name conversion; transfer presets; selecting by mask; a filter window with history.
14. FE, hashes and sizes without a shell, small: open. Checksums over SFTP and FTP; size and checksum tabs in Properties; folder sizes in the file list.
15. FF, synchronize, huge: open. The full synchronize: directions, mirror, criteria, a checklist before applying, moved files, deletes, masks, through the queue, remembered settings, and two local folders.
16. FI, tabs and workspaces, medium: open. Tab extras, workspaces, and copy and paste between tabs.
17. FJ, the connection list, medium: open. Folders in the list, notes, type-to-find, import of the SSH config, and opening or generating a URL.
18. FK, SSH options, large: open. Server command such as sudo, timeout and IP version, proxy, jump host, agent and certificates, an expired password, and algorithm order.
19. FL, FTP and server details, medium: open. Implicit TLS, active or passive mode, keepalives, automatic reconnect, time zone, encoding, and link options.
20. FN, find and edit, large: open. A richer find window, robust edit watching, editors per file type, and a built-in text editor.
21. FO, shell tools and keeping a folder up to date, large: open. A second shell session, a console window, a command line, custom commands, commands after login, and keeping a remote folder up to date.
