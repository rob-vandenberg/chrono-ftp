# STATUS.md

## Now

Version 0.0.9: an optional colour per connection, one login window, and the most height for the file lists, with a queue that hides when idle unless pinned. Since 0.0.5: one shared file list with sorting, drag and drop, buttons per side, a path line, a count line, rights and link target, a filter, Edit, Synchronize, actions on this computer, remembered sizes, tidying of leftovers, names that are not valid UTF-8, and the connection colour in the terminal.

Tested on a real test account over SFTP, without a shell, and on a real SSH server on this computer. Not yet tested on a real FTP server: that needs a login the program can use with a person typing the password.

## Next task

The owner tries it on real work. Then: the owner's findings, the test against a real FTP server, and the import against a real export.

## Open questions

1. Is `rsync` over SSH fast enough against FTP? Waits for the test on a real FTP server.
2. [Not tested] An FTP connection may need a second login to browse during a transfer.
3. Right after a folder is made, `setRootPath` once gave an invalid index in a probe. The tree of the left side still uses that call.
4. Known limits: over FTP a link can't be resolved, so a stop while writing through one leaves a half file; the folder tree of the left side can't show a folder whose name is not valid UTF-8, though the list can; a server file can't be dragged to a file manager; Properties works only on the right side; a name that is not valid UTF-8 can't be opened in another program or sent to the Trash; the colour of the terminal has not been seen on screen.
