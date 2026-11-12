# STATUS.md

## Now

Version 0.0.36: the title bar shows the version, "Chrono FTP 0.0.36" (D-059).

Before that, version 0.0.35: F1 opens the About window, with the program icon, the version, what Chrono FTP is and its license (D-059).

Before that, version 0.0.34: block FH, navigation. The path above each side is a row of folder parts, each opens its folder; a click on the empty part or Ctrl+L types a path, Escape returns; the path of the focused side is highlighted, and on the right its hover text names user@host; a long path makes room from the left (D-055). Ctrl+B makes the folder of the focused side a bookmark, Ctrl+O opens a folder with the paths typed before and the bookmarks of that side, each side has a Bookmarks button, remote bookmarks belong to their connection or to all, and a key can open a bookmark; location profiles open a local and a remote folder with one click from Location Profiles on the top bar; one window organizes both, kept in `bookmarks.json` (D-056). Ctrl+. opens the folder of the focused side on the other side; Sync Browsing, per tab, makes each step on the other side too, makes a missing folder after a question when its parent is there, turns itself off and says why when it can't follow, and turns off when its connection ends (D-057). A typed path (Ctrl+L or Ctrl+O, also in the bookmarks window) is read from the folder shown on both sides, keeps the real bytes of a name that is not valid UTF-8, and one that leads nowhere is told calmly. A deleted connection takes its own bookmarks along; Reconnect logs in as the connection is saved now. A connection has a local starting folder and "Remember the last folders"; a starting folder that is gone says so calmly and opens the home folder (D-058).

Before that, version 0.0.33: not connected, the right side is dark grey, the darker row colour of the left side, with light text; a dropped tab stays light grey (D-007). Before that, version 0.0.32: Connect is the default button of the connections window, so Enter connects the selected connection (D-050). Before that, version 0.0.31: the connection that last went live is remembered, and at start the connections window opens on it, selected and in view; when it is gone, the window starts as before (D-053). Before that, version 0.0.30: the right side shows the connection colour again: the menu style is given to each menu, not to the whole program, where it kept the right side grey; the menu border is a visible grey (D-052). Before that, version 0.0.29: every right-click menu lets go of the keyboard once it is open, so the desktop's shortcuts, such as a screenshot, work while a menu is open (D-052); menus have a border, room around them and roomy items.

Before that, 0.0.28: the menu of a tab is a menu of normal width with an icon, and its entry says "Edit settings…".

Before that, 0.0.27: a right-click on a tab shows Edit, which opens the connections window on that connection; greyed with its reason when the connection is no longer in the list.

Before that, 0.0.26: Connect on a connection that has a tab logs in again there with changed settings, asking first about its running transfers; with the same settings it only switches, and reconnects a dropped one (D-051).

Before that, 0.0.25: one Chrono FTP at a time: a second start brings the running one to the front (D-051); opening a connection that already has a tab switches to it; Connect with unsaved changes in the connections window asks "Save changes?" first.

Before that, 0.0.24: the tabs and New Tab share one centre line.

Before that, 0.0.23: the icons of the side bars (walking through folders and the actions of each side) are 24 pixels instead of 18, drawn at that size.

Before that, 0.0.22: without tabs, New Tab stands at the left of its row; the empty tab bar no longer leaves a hole in front of it.

Before that, 0.0.21: the password fields of the connections window and the login window have an eye that shows or hides what was typed; it shows only while the field holds typed text, so a stored password is never shown.

Before that, 0.0.20: the labels of the connections window keep their room from the splitter again.

Before that, 0.0.19: Import… and Export… stand next to each other on the left under the list of the connections window.

Before that, 0.0.18: the buttons under the list and under the fields of the connections window stand on one line.

Before that, 0.0.17: the connections window is laid out as the reference program: Import… and Export… (greyed for now) under the list; Connect, Duplicate and Delete in the right-click menu of a connection; New, Save and Connect under the fields, which can now be made narrow. Duplicate saves a copy with its password, ready to be renamed (D-050).

Before that, 0.0.16: the connections window has a splitter between its list and the fields of the selected connection, and the splitter is where it was last time.

Before that, 0.0.15: the connections window opens with the size and place it had last time, also after a run in which it was not opened.

Before that, 0.0.14: the program icon. Chrono FTP has its own icon (a folder with FTP and the orange clock of the Chrono programs, `src/chrono_ftp/ui/resources/chrono-ftp.svg`) on every window, the taskbar and Alt+Tab; the windows belong to the start-menu entry `chrono-ftp`.

Before that, 0.0.13: block FC, the queue. Every item can be paused and resumed without losing what arrived, moved, run now, retried where it can continue, stopped, deleted and given a speed limit of its own that holds at once; done items leave the list after a while, and an error goes on or pauses the queue, as Settings say (D-046). A choice in the queue says what happens when it is done: disconnect, close Chrono FTP, suspend or shut down, once, after a countdown that can be cancelled, and never while edited files are not on the server yet (D-047). Several items run at once ("Transfers at once", default 2), never two on the same place, one question at a time, the speed limit shared, and a server that takes no more logins makes an item wait calmly instead of failing (D-048). A large file can go over several connections at once, as one item, where the person chooses it (D-049).

Before that, 0.0.12: transfer options, resume from part files and the overwrite question (D-043 to D-045). 0.0.11: the main window in parts (D-041, D-042). 0.0.10: FTP listings without `OPTS MLST`, an optional colour per connection, one login window, and the queue hidden when idle unless pinned.

Tested by the owner on real work: the connection and terminal colours, the start menu entry, the import of a real export, and SFTP on the backup server. Tested on a real test account over SFTP, without a shell, and on a real SSH server on this computer. For 0.0.13 the live tests ran on the real FTP test account (several transfers at once, a download in parts) and on this computer's SSH server (a file in parts both ways).

## Next task

Block FM of the private `FEATURES.md`: Properties and server actions, the next in the order of D-054.

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
7. Known limits of navigation (0.0.34):
   1. A bookmark or location profile belongs to a connection by its name; a connection renamed in the connections window takes them along, also while its tab is open; a duplicate does not.
   2. The live tests of navigation ran on this computer's SSH server only (`tests/ui/test_ui_live_navigation.py`).
