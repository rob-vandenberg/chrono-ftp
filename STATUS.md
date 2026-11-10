# STATUS.md

## Now

Version 0.0.18: the buttons under the list and under the fields of the connections window stand on one line.

Before that, 0.0.17: the connections window is laid out as the reference program: Import… and Export… (greyed for now) under the list; Connect, Duplicate and Delete in the right-click menu of a connection; New, Save and Connect under the fields, which can now be made narrow. Duplicate saves a copy with its password, ready to be renamed (D-050).

Before that, 0.0.16: the connections window has a splitter between its list and the fields of the selected connection, and the splitter is where it was last time.

Before that, 0.0.15: the connections window opens with the size and place it had last time, also after a run in which it was not opened.

Before that, 0.0.14: the program icon. Chrono FTP has its own icon (a folder with FTP and the orange clock of the Chrono programs, `src/chrono_ftp/ui/resources/chrono-ftp.svg`) on every window, the taskbar and Alt+Tab; the windows belong to the start-menu entry `chrono-ftp`.

Before that, 0.0.13: block FC, the queue. Every item can be paused and resumed without losing what arrived, moved, run now, retried where it can continue, stopped, deleted and given a speed limit of its own that holds at once; done items leave the list after a while, and an error goes on or pauses the queue, as Settings say (D-046). A choice in the queue says what happens when it is done: disconnect, close Chrono FTP, suspend or shut down, once, after a countdown that can be cancelled, and never while edited files are not on the server yet (D-047). Several items run at once ("Transfers at once", default 2), never two on the same place, one question at a time, the speed limit shared, and a server that takes no more logins makes an item wait calmly instead of failing (D-048). A large file can go over several connections at once, as one item, where the person chooses it (D-049).

Before that, 0.0.12: transfer options, resume from part files and the overwrite question (D-043 to D-045). 0.0.11: the main window in parts (D-041, D-042). 0.0.10: FTP listings without `OPTS MLST`, an optional colour per connection, one login window, and the queue hidden when idle unless pinned.

Tested by the owner on real work: the connection and terminal colours, the start menu entry, the import of a real export, and SFTP on the backup server. Tested on a real test account over SFTP, without a shell, and on a real SSH server on this computer. For 0.0.13 the live tests ran on the real FTP test account (several transfers at once, a download in parts) and on this computer's SSH server (a file in parts both ways).

## Next task

Block FD of the private `FEATURES.md`: masks.

## Open questions

1. Is `rsync` over SSH fast enough against FTP? Waits for the test on a real FTP server.
2. [Not tested] An FTP connection may need a second login to browse during a transfer.
3. Right after a folder is made, `setRootPath` once gave an invalid index in a probe. The tree of the left side still uses that call.
4. Known limits: over FTP a link can't be resolved, so a stop while writing through one leaves a half file; the folder tree of the left side can't show a folder whose name is not valid UTF-8, though the list can; a server file can't be dragged to a file manager; Properties works only on the right side; a name that is not valid UTF-8 can't be opened in another program or sent to the Trash.
5. Known limits of the transfers (0.0.13):
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
   13. "The same place" of two queue items on a server means the same login: an FTP and an SFTP tab, or two users, on one machine count as different sides.
   14. With a status bar speed limit set, every start or end of another item restarts running rsync items, as their share changes; only time is lost. A whole-folder rsync run without a look-over and without kept dates can send earlier files again after a limit change or a resume.
   15. Over a real SSH server, the server's rsync may put its unfinished copy away a moment after Chrono FTP removed that folder (resume Never); Delete tries again, and tidying takes the rest an hour later.
   16. A file split in parts asks for its further logins only when it starts; places that free up later are not used for it.
6. The live test on the SFTP test account without a shell has not run for 0.0.12 and 0.0.13.
