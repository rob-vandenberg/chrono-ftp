# STATUS.md

## Now

Version 0.0.5: the first working version. Connections with colours in tabs, each tab with its own local and remote folder, SSH and FTP, transfers with a queue and a speed limit, remote actions, recursive owner, group and permissions, search, terminal and import of a session list.

Tested against real servers inside the tests, and over SSH on this computer. Not yet tested against the other machines: they need a login from the owner.

## Next task

The owner tries it on real work. Then: the test against a real FTP server, the import against a real export, and the owner's findings.

## Open questions

1. Is `rsync` over SSH fast enough against FTP? To be measured first in milestone 2.
2. [Not tested] `rsync` and the terminal use the system `ssh`, apart from the `asyncssh` login. A connection with a stored password may need SFTP for its transfers. To be checked in milestone 2.
3. [Not tested] An FTP connection may need a second login to browse during a transfer. To be checked when the connection layer is designed.
4. File names that are not valid UTF-8 don't appear on the local side, without a message. To be decided in milestone 2, with the transfers.
5. Right after a folder is made, `setRootPath` once gave an invalid index in a probe. The cause isn't known, and no person can reach this yet.
6. Known limits of this version: over FTP a link can't be resolved, so a stop while writing through one leaves a half file; links inside a folder tree sent by `rsync` become files; a drop can leave a `.chrono-ftp-part` or `.chrono-ftp-old` file behind, never a lost file; a stalled SFTP transfer only ends through the keepalive; the terminal has no connection colour yet; there is no drag and drop yet.
