# STATUS.md

## Now

Version 0.0.10: FTP listings on servers without the option `OPTS MLST`, an optional colour per connection, one login window, and the most height for the file lists, with a queue that hides when idle unless pinned. Since 0.0.5: one shared file list with sorting, drag and drop, buttons per side, a path line, a count line, rights and link target, a filter, Edit, Synchronize, actions on this computer, remembered sizes, tidying of leftovers, names that are not valid UTF-8, and the connection colour in the terminal.

Tested by the owner on real work: the connection and terminal colours, the start menu entry, the import of a real export, and SFTP on the backup server. Tested on a real test account over SFTP, without a shell, and on a real SSH server on this computer. FTP on a real server still waits for the owner to try it, because the password has to be typed by a person.

## Next task

Block FA of the private `FEATURES.md`: the main window split into modules, so the blocks after it fit (D-001, OR-8). Waiting on the owner: trying FTP on the backup server.

## Open questions

1. Is `rsync` over SSH fast enough against FTP? Waits for the test on a real FTP server.
2. [Not tested] An FTP connection may need a second login to browse during a transfer.
3. Right after a folder is made, `setRootPath` once gave an invalid index in a probe. The tree of the left side still uses that call.
4. Known limits: over FTP a link can't be resolved, so a stop while writing through one leaves a half file; the folder tree of the left side can't show a folder whose name is not valid UTF-8, though the list can; a server file can't be dragged to a file manager; Properties works only on the right side; a name that is not valid UTF-8 can't be opened in another program or sent to the Trash.
5. [Not understood yet] About one run in five, `tests/ui/test_ui_sessions.py::test_a_save_while_the_connection_is_gone_goes_after_reconnect` ends with `RuntimeError: Signal source has been deleted` at clean-up: a thread answers a window that is already deleted. Marking the bridge closed at window close did not stop it, so the cause is still to be found.
