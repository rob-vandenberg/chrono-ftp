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
4. One-time import of a session list: done (0.0.5), but not yet checked against a real export.
5. The folder `packaging/` for the first installer: open.
6. Compare local and remote files: postponed, the first thing to drop.
