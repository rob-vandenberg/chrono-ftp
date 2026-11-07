# STATUS.md

## Now

Version 0.0.3: the main window. Local on the left with a working folder tree and file list, the right side plain grey and "Not connected", the empty tab bar above both sides. No connections yet.

## Next task

`ROADMAP.md`, milestone 1: copy the Breeze icons of D-028.

## Open questions

1. Is `rsync` over SSH fast enough against FTP? To be measured first in milestone 2.
2. [Not tested] `rsync` and the terminal use the system `ssh`, apart from the `asyncssh` login. A connection with a stored password may need SFTP for its transfers. To be checked in milestone 2.
3. [Not tested] An FTP connection may need a second login to browse during a transfer. To be checked when the connection layer is designed.
4. File names that are not valid UTF-8 don't appear on the local side, without a message. Not decided by the owner.
5. Browsing the local side doesn't go through an engine command yet, while OR-16 says every user action does. Not decided by the owner.
6. Right after a folder is made, `setRootPath` once gave an invalid index in a probe. The cause isn't known, and no person can reach this yet.
