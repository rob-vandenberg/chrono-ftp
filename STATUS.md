# STATUS.md

## Now

Version 0.0.11: block FA. The main window is built from parts, one module per group of actions, so the blocks after it stay under 2,000 lines per file (D-041); nothing the person sees changed. A closed window takes no more answers from its connection threads (D-042), and the tests with a transfer wait for its end, which ended the unstable test of the sessions file.

Before that, 0.0.10: FTP listings on servers without the option `OPTS MLST`, an optional colour per connection, one login window, and the most height for the file lists, with a queue that hides when idle unless pinned.

Tested by the owner on real work: the connection and terminal colours, the start menu entry, the import of a real export, and SFTP on the backup server. Tested on a real test account over SFTP, without a shell, and on a real SSH server on this computer. FTP is tested against the fake server in the tests; the test on a real FTP server comes later, by the owner's decision.

## Next task

Block FB of the private `FEATURES.md`: transfer options.

## Open questions

1. Is `rsync` over SSH fast enough against FTP? Waits for the test on a real FTP server.
2. [Not tested] An FTP connection may need a second login to browse during a transfer.
3. Right after a folder is made, `setRootPath` once gave an invalid index in a probe. The tree of the left side still uses that call.
4. Known limits: over FTP a link can't be resolved, so a stop while writing through one leaves a half file; the folder tree of the left side can't show a folder whose name is not valid UTF-8, though the list can; a server file can't be dragged to a file manager; Properties works only on the right side; a name that is not valid UTF-8 can't be opened in another program or sent to the Trash.
