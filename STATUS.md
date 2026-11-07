# STATUS.md

## Now

Version 0.0.2: the project start. The repository, documents, rules and layers exist; there is no program function yet.

## Next task

`ROADMAP.md`, milestone 1: the main window with the fixed left and right side.

## Open questions

1. Is `rsync` over SSH fast enough against FTP? To be measured first in milestone 2.
2. [Not tested] `rsync` and the terminal use the system `ssh`, apart from the `asyncssh` login. A connection with a stored password may need SFTP for its transfers. To be checked in milestone 2.
3. [Not tested] An FTP connection may need a second login to browse during a transfer. To be checked when the connection layer is designed.
