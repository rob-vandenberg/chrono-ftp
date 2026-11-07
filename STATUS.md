# STATUS.md

## Now

Version 0.0.4: the main window, and the Breeze icons ready for the buttons. Local on the left with a working folder tree and file list, the right side plain grey and "Not connected", the empty tab bar above both sides. No connections yet.

## Next task

`ROADMAP.md`, milestone 1: the connection list with colours and the connection dialogs.

## Open questions

1. Is `rsync` over SSH fast enough against FTP? To be measured first in milestone 2.
2. [Not tested] `rsync` and the terminal use the system `ssh`, apart from the `asyncssh` login. A connection with a stored password may need SFTP for its transfers. To be checked in milestone 2.
3. [Not tested] An FTP connection may need a second login to browse during a transfer. To be checked when the connection layer is designed.
4. File names that are not valid UTF-8 don't appear on the local side, without a message. To be decided in milestone 2, with the transfers.
5. Right after a folder is made, `setRootPath` once gave an invalid index in a probe. The cause isn't known, and no person can reach this yet.
