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

One milestone per block of the private `FEATURES.md` (D-001, OR-13), in this order:

5. FA, the main window split into modules: open.
6. FB, transfer options: open.
7. FC, the queue: open.
8. FD, masks: open.
9. FE, hashes and sizes without a shell: open.
10. FF, synchronize: open.
11. FG, selection and keys: open.
12. FH, navigation: open.
13. FI, tabs and workspaces: open.
14. FJ, the connection list: open.
15. FK, SSH options: open.
16. FL, FTP and server details: open.
17. FM, Properties and server actions: open.
18. FN, find and edit: open.
19. FO, shell tools and keeping a folder up to date: open.
20. FP, program-wide: open.
21. FQ, looks: open.
