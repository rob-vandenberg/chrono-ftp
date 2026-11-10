# STATUS.md

## Now

Version 0.0.12: block FB, transfer options. Before every upload, download and drop a transfer window shows the target folder and the options of that transfer, which the person can change for this one transfer; Settings keeps the defaults and can switch the window off (D-043). A stopped or broken-off transfer continues where it stopped, only from the very file it came from (D-044). A file that is there already is asked about, and an answer holds only for the file as the person saw it: nothing that changed meanwhile is replaced, and a version saved in the last moment is kept aside and named (D-045). Dates, rights, only new and updated, count first and move work on both ways, rsync and file by file, and what a server can't do is greyed out and says why.

Before that, 0.0.11: the main window in parts (D-041), and a closed window takes no more answers (D-042). 0.0.10: FTP listings on servers without the option `OPTS MLST`, an optional colour per connection, one login window, and the most height for the file lists, with a queue that hides when idle unless pinned.

Tested by the owner on real work: the connection and terminal colours, the start menu entry, the import of a real export, and SFTP on the backup server. Tested on a real test account over SFTP, without a shell, and on a real SSH server on this computer. FTP is tested against the fake server in the tests; the test on a real FTP server comes later, by the owner's decision.

## Next task

Block FC of the private `FEATURES.md`: the queue. Open item that belongs to it: `TransferQueue.close()` stops every job but does not wait for its thread to end.

## Open questions

1. Is `rsync` over SSH fast enough against FTP? Waits for the test on a real FTP server.
2. [Not tested] An FTP connection may need a second login to browse during a transfer.
3. Right after a folder is made, `setRootPath` once gave an invalid index in a probe. The tree of the left side still uses that call.
4. Known limits: over FTP a link can't be resolved, so a stop while writing through one leaves a half file; the folder tree of the left side can't show a folder whose name is not valid UTF-8, though the list can; a server file can't be dragged to a file manager; Properties works only on the right side; a name that is not valid UTF-8 can't be opened in another program or sent to the Trash.
5. Known limits of the transfers (0.0.12):
   1. On FTP, a file that appears between the last check and the rename can be replaced.
   2. An FTP server that lists a link as a plain file can't be told apart: an upload replaces such a link with a file.
   3. An FTP server that gives neither a type of its own nor a full mode shows a pipe as a plain file, and a download of it waits until it is stopped.
   4. On FTP, and on SFTP servers without `fsync@openssh.com`, a move checks the arrived copy by its size and date only; the hover text says so.
   5. An append broken off by a lost connection is left as it is, unless nothing else can be in the file.
   6. On FTP without MLST, the listing kept during a job doesn't see what others add meanwhile; the check before each rename does.
   7. Whether an SFTP server can put a file on its disk is read from a private attribute of asyncssh; if that changes, the program says it checked size and date only.
   8. The notes of append owners use a lock file; on Windows the notes of other processes count as ended.
   9. On the rsync way, a file agreed to be replaced is looked at again right before rsync starts; a change during rsync's own run is not seen.
   10. With "Ask before replacing" off, rsync skips a file whose size and date match, although its content may differ.
   11. Names of our own are kept within 255 bytes; a file system with a lower limit can still refuse them.
   12. On FTP, a picked file that the server refuses to tell about can't be told from one that is gone: both answer 550, and the job says it is no longer on the server.
6. The live test on the SFTP test account without a shell has not run for 0.0.12.
