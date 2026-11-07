# STATUS.md

## Now

Version 0.0.1: the project start. The repository, documents, rules and layers exist; there is no program function yet.

## Next task

`ROADMAP.md`, milestone 1: the main window with the fixed left and right side.

## Open questions

1. What happens on a connection without a shell (SFTP only, FTP)? Recursive owner, group and permissions: greyed out (D-016), or a slow per-file loop with a warning. Rename, move and delete: through the protocol? Copy and search: greyed out? Not decided by the owner.
2. Is `rsync` over SSH fast enough against FTP? To be measured first in milestone 2.
3. [Not tested] `rsync` and the terminal use the system `ssh`, apart from the `asyncssh` login. A connection with a stored password may need SFTP for its transfers. To be checked in milestone 2.
4. [Not tested] An FTP connection may need a second login to browse during a transfer. To be checked when the connection layer is designed.
5. What does a tab look like when its connection dropped: grey like the right side (OR-3)? Does the tab bar run above both sides, since a tab changes both? Not decided by the owner.
