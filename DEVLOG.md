# DEVLOG.md

Append-only log, one entry per completed task, newest last.

## 0.0.1 (2026-11-07): project start

The repository, the documents, the rules, the layers, the private push tools and the first tests. No program function yet.

## 0.0.2 (2026-11-07): the owner's answers

Actions that need a shell (D-027), the grey tab of a dropped connection, the tab bar above both sides, Breeze icons (D-028), and the license AGPL-3.0 only. Still open: the three questions in `STATUS.md`.

## 0.0.3 (2026-11-07): the main window

The window test plugin, `core/colours.py`, the main window with the tab bar above both sides, the local side with folder tree and file list, the grey right side. Comparing by size and date works on every connection; only `sha256sum` needs a shell (D-020, D-027).

## 0.0.4 (2026-11-07): Breeze icons, and OR-16 removed

The icons of D-028 with their licence file and `ui/icons.py`; no button uses them yet. OR-16 is gone: server work stays out of the window, pure window actions stay in it (D-022).

## 0.0.5 (2026-11-07): the first working version

Milestones 1, 2 and 3 in one go, on the owner's word to continue without stopping. Measured on this computer with a throwaway tree: chmod over 5,000 files in 0.04 s; rsync 276 MB/s; SFTP 121 MB/s before the switch to asyncssh's pipelined copy.

## 0.0.6 (2026-11-07): daily use

One shared file list on both sides, with buttons per side after the owner's picture of the goal, drag and drop, Edit, Synchronize, local actions, tidying of leftovers, and names that are not valid UTF-8. The real window ran against a real test account over the network: every step passed at about 100 MB/s. A real SSH server on this computer confirmed that a link deep in a target tree stays a link and that a name that is not valid UTF-8 can be listed, fetched and deleted.

## 0.0.7 (2026-11-07): height for the file lists

One top row and one button row per side; the queue under a separator, hidden when idle unless pinned, and open after a failure. In a window 800 pixels high the lists went from about 410 to 661 pixels with the queue hidden.

## 0.0.8 (2026-11-07): the login window

User and password in one window; a connection without a user asks before its first try; a typed user is kept once the server accepted it.

## 0.0.9 (2026-11-07): the colour is optional

A new connection has no colour; a tick in the Connections window switches it on. A live connection without a colour looks like the left side, with a neutral tab and the normal terminal colours; not connected stays plain grey.

## 0.0.10 (2026-11-07): FTP listings without OPTS MLST

The listing asked the server for a list of details first, which a real server refused with 500, and that was read as having no MLSD at all. A refusal of that request is now ignored: the listing goes on, and a server that has the option still sends rights and owner.

## 0.0.11 (2026-11-07): block FA, the main window in parts

`ui/mainwindow.py` went from 1,285 lines to about 270: every group of actions moved, unchanged, into a module of its own (D-041); a syntax-tree comparison confirmed that all 115 methods, functions, classes and numbers are the same. The unstable test of the sessions file had two causes: four tests ended when the file arrived, before its transfer had ended, so closing the window asked a question nobody answered; and a thread could still answer a deleted window. The tests now wait for the end of their transfers, and a closed window takes no more answers (D-042). Twenty runs of the sessions file in a row passed without a warning.

## 0.0.12 (2026-11-10): block FB, transfer options

The transfer window before every upload, download and drop, with Transfer settings for one transfer and the defaults in Settings (D-043); resume from part files with notes, kept and tidied by rule, with names of our own within 255 bytes (D-044); the overwrite question, whose answers hold only for the file as the person saw it, on both ways (D-045); dates, rights, only new and updated, count first and move, greyed where a server can't do them. rsync is preceded by a look-over wherever a file is asked about, runs in a process group of its own, and is never fed a file name as progress. Pipes, sockets, devices and links to folders are never sent, fetched or replaced as files, and are told, never left out in silence.

Twenty-one review rounds: the first twenty found real faults, among them a rename onto a link that let rsync replace the kept file, a fallback after rsync could not log in that replaced a file changed after the question, a last-moment save that the replace removed, part files lost or left without a note, a picked item gone from the server that made an empty folder here, and test children and server threads that outlived their tests. Each fault came with a test that fails on the code before it; surviving mutants of the review rounds are killed by tests, the few that remain are equivalent. Round 21 found nothing real; its two untrue texts (a link to a place that can't be opened, and a server that refuses to tell about a file) were fixed.

Still open: the known limits of the transfers in `STATUS.md`; `TransferQueue.close()` does not wait for its thread (block FC); the live test on the SFTP test account without a shell has not run for this version.

## 0.0.13 (2026-11-11): block FC, the queue

Queue control per item (D-046): pause and resume keeping what arrived, move up and down, run now, retry, stop and Stop All, delete, a speed limit per item that holds at once (rsync starts again with it), finished items that leave after a while, an error that goes on or pauses the queue, and `TransferQueue.close()` that waits for every thread and lets nothing reach the window afterwards. When the queue is done (D-047): stay, disconnect, close, suspend or shut down, once, after a countdown, held back by unsent edits. Several transfers at once (D-048): a scheduler thread and a thread per item, a login per FTP item, never two items on the same place, one question at a time with an answer for all carried over, the speed limit shared, and a server that takes no more logins making an item wait calmly; the fake SSH server of the tests now leaves no thread or descriptor behind. One large file in parts (D-049): up to four logins writing their own ranges into one part file, as one item, with the ranges noted for a resume.

Many review rounds over the three parts; every real fault came with a test that fails on the code before it, among them a job started twice while its previous run still ended, two items left waiting for good after logins refused at the same moment, a later item overtaking an earlier one on its place, an FTP login after a broken-off file that failed instead of waiting, and a split part that gave up after a few drops spread over a long file. Live tests passed on the real FTP test account (three transfers at once both ways, a download in parts) and on this computer's SSH server (a file in parts both ways).

Still open: the known limits of the transfers in `STATUS.md`; the live test on the SFTP test account without a shell has not run for this version.

## 0.0.14 (2026-11-10): the program icon

Chrono FTP has its own icon, made by the owner in the style of the Chrono programs: a blue square, a white folder with the letters FTP and the orange clock. The program ships it (`src/chrono_ftp/ui/resources/chrono-ftp.svg`, in the package data) and sets it for every window, the taskbar and Alt+Tab; the windows tell the desktop they belong to the start-menu entry `chrono-ftp`. A test checks that the icon draws its blue, white and orange.

Still open: the first installer must install the icon (`packaging/` does not exist yet).

## 0.0.15 (2026-11-10): the connections window keeps its size and place

The size and place of the connections window are kept in the window file with the main window's sizes, when the main window closes; a run that does not open it keeps the place it had. A saved place on a screen that is gone is brought back onto a screen by Qt. Two tests: after a restart, and after a run without the window.

## 0.0.16 (2026-11-10): a splitter in the connections window

The list of connections and the fields of the selected connection have a splitter between them; a wider window widens the fields. Its place is kept in the window file with the size and place of the window. A test: the splitter is where it was after a restart.

## 0.0.17 (2026-11-10): the connections window as the reference program

Import… and Export… (greyed until export exists, with a hover text) under the list; Connect, Duplicate and Delete in the right-click menu of a connection; New, Save and Connect under the fields. The row of five buttons under the fields is gone, so the fields side shrinks to about 300 pixels instead of 492. Duplicate saves a copy named "… (copy)" with its password, selected and ready to be renamed (D-050). Tests for Duplicate, the menu, the buttons under the list and the narrow fields.

## 0.0.18 (2026-11-10): the buttons of the connections window on one line

The fields side had its own top and bottom margin and the list side none, so its buttons stood higher than Import… and Export…. Both sides now start and end at the same height; a test checks that all five buttons stand on one line.

## 0.0.19 (2026-11-10): Import… and Export… side by side

Export… stands right next to Import… on the left under the list of the connections window, instead of at the right edge; a test checks it.

## 0.0.20 (2026-11-10): room between the splitter and the labels

0.0.18 meant to keep the left margin of the fields side, but read it before the layout was on its widget, where it is 0, so the labels touched the splitter. The margin is now the style's own; top and bottom stay 0, so the buttons stay on one line. A test checks the room.

## 0.0.21 (2026-11-10): an eye in the password fields

The password fields of the connections window and the login window have an eye (the Breeze icons view-visible and view-hidden) that shows or hides what was typed. It appears only while the field holds typed text; an emptied field, also when another connection is chosen, hides the password again. A stored password is never in the field, so it can never be shown. Tests in `tests/ui/test_ui_passwordeye.py`.

## 0.0.22 (2026-11-10): no hole in front of New Tab

An empty tab bar kept 123 pixels free for its scroll arrows, a hole in front of New Tab. The tab bar now shows only while it holds a tab; the row keeps the height of a tab, so nothing below it jumps. A test checks both.

## 0.0.23 (2026-11-10): bigger icons in the side bars

The icons of the navigation and action bars of both sides are 24 pixels instead of 18. The icons are now also drawn at 24 and 48 pixels, so they stay sharp. The general bar and the queue bar stay at 18. A test checks the size.

## 0.0.24 (2026-11-10): the tabs and New Tab on one line

The tab bar had a fixed height of two text lines and drew its tabs at its top, while New Tab stood centred in the row, so New Tab sat lower than the tabs. The tab bar now has its own height and stands centred in the row; the row keeps the height of two lines. A test checks that a tab and New Tab share one centre line.

## 0.0.25 (2026-11-10): one Chrono FTP, one tab per connection, and Save changes?

A second start of Chrono FTP brings the running one to the front through a local socket per user and ends; a socket left behind by a crash is removed (D-051). A connection that already has a tab is switched to instead of opened again. Connect in the connections window with unsaved changes, or a typed password, asks "Save changes to …?": Save saves and connects, Don't Save connects with the saved connection and shows it again, Cancel changes nothing. Tests for each.

## 0.0.26 (2026-11-10): Connect on an open connection uses its new settings

0.0.25 only switched to the tab of a connection that was already open, so new settings had no effect (the owner's correction). Now changed settings log in again in that tab, after asking about its running transfers; the same settings only switch, and reconnect a dropped connection (D-051, its older wording kept). Four tests.

## 0.0.27 (2026-11-10): Edit in the right-click menu of a tab

A right-click on a tab shows a menu with Edit, which opens the connections window with that tab's connection selected; Save and Connect there log in again with the new settings (0.0.26). Edit is greyed with a hover text when the connection is no longer in the list; beside the tabs there is no menu. Two tests.

## 0.0.28 (2026-11-10): a proper menu on a tab

The right-click menu of a tab was as narrow as its one short entry. It now has the width of a normal menu (22 average characters), its entry has the Breeze icon document-properties, and it says "Edit settings…".

## 0.0.29 (2026-11-10): menus that leave the keyboard free, and a proper look

On X11 an open menu held the whole keyboard, so the owner's screenshot shortcut did nothing. Every right-click menu (file lists, queue, tabs, connections window) now opens through `open_menu` in `src/chrono_ftp/ui/menus.py`, which lets go of the keyboard once the menu is open and keeps the mouse; tried first on the tab menu, where the screenshot of the open menu worked (D-052). Menus have Chrono Mail's look: a border, padding and roomy items. Four tests.

## 0.0.30 (2026-11-10): the connection colour is back, and a visible menu border

Since 0.0.29 the right side stayed grey on a live connection, with the text in the colour's readable shade: the menu style sheet was set on the whole program, and with it the file list kept its first background. `open_menu` in `src/chrono_ftp/ui/menus.py` now gives the style to each menu; `theme.apply` sets none. The menu border was the bevel colour, almost the colour of the window; it is now `MENU_BORDER` (#4a5157) in `src/chrono_ftp/core/colours.py` (D-052). Two tests, both failing on 0.0.29. Review round 2 fixed: settings files written escaped, so a folder name that is not valid UTF-8 is kept, and no .tmp left on a failed write; the path field keeps the real path, never the no-access note, survives a refresh while typing, and gives the focus back to the list; folders typed in the bookmarks window are read as in the path field; a partly damaged bookmarks file is set aside once; a deleted connection forgets its own bookmarks; Reconnect uses the connection as saved now; a move up because a folder went away is no step; bookmark keys never take a standard key binding or a Ctrl key lists use. Still open: the window sizes that come back at their defaults after a restart.

## 0.0.31 (2026-11-10): the connections window starts on the last connection

The connections window started on the top item of its list. The connection that goes live is now written at once to `last_connection` in `settings.json`, and at start the window selects it, scrolls it into view and shows its fields; a deleted or renamed one leaves the window as before, and the settings window keeps the value (D-053). Four tests, all failing on 0.0.30. Still open: the window sizes that come back at their defaults after a restart.

## 0.0.32 (2026-11-10): Enter connects

In the connections window Enter did not connect, and a focused button such as Import… took Enter itself. Connect is now the default button and no other button takes Enter, so Enter in the list, in a field or on a focused button connects the selected connection (D-050). Four tests, all failing on 0.0.31. Still open: the window sizes that come back at their defaults after a restart.

## 0.0.33 (2026-11-10): a dark right side while not connected

Without a connection the right side was light grey (#8a8f94) with dark text. It is now plain dark grey, the darker row colour of the left side (#151c1c), without alternating rows, with light text and the usual selection; the tab of a dropped connection keeps its light grey as `NOT_CONNECTED_TAB` (D-007). One test changed, failing on 0.0.32; the tab tests follow the new name. Still open: the window sizes that come back at their defaults after a restart.

## 0.0.34 (2026-11-12): block FH, navigation

The plain path field of each side became a clickable path (`ui/pathbar.py`): folder parts, a field to type in on a click on the empty part or Ctrl+L, the focused side highlighted, user@host as hover text on the right, shortened from the left (D-055). Bookmarks and location profiles in `bookmarks.json` (`config/bookmarks.py`): Ctrl+B, the Open folder window of Ctrl+O (`ui/openfolder.py`), a Bookmarks button per side, Location Profiles on the top bar, and one window to organize them with an optional key per bookmark (`ui/bookmarksdialog.py`); a damaged file is set aside, an unreadable one never overwritten (D-056). Ctrl+. and synchronized browsing per tab (`engine/browsing.py`, `ui/windownavigation.py`), which makes a missing folder after a question and turns itself off with its reason (D-057). A local starting folder and "Remember the last folders" per connection; a starting folder that is gone says so calmly (D-058). New tests: `tests/test_config_bookmarks.py`, `tests/ui/test_ui_navigation.py`, opt-in `tests/ui/test_ui_live_navigation.py` (run on this computer's SSH server), and new cases in `tests/test_config_connections.py`; the test of a gone starting folder answers its message now. Review round 1 fixed: a typed path (Ctrl+L) read from the folder shown on both sides as Ctrl+O reads it, and told when it leads nowhere; a connection saved with its tab open reaches that tab; bookmark keys need Ctrl, Alt or a free F-key; a step whose parent folder is missing turns synchronized browsing off at once, a refused folder gives the server's reason; a remote typed path is kept only once it opened; an ended connection turns synchronized browsing off; a click on a path part gives that side the focus; an & in a name stays in menus; another window taking the focus keeps the typing; a step the person moved on from is forgotten; and the test of two tabs on one login opens two differently named connections. Review round 3 fixed: a relative local starting folder is read from the home folder; a dropped tab's path can't be typed in or clicked; a set-aside copy is private and made once for the same damage; Ctrl+L while typing keeps what is typed. Still open: the window sizes that come back at their defaults after a restart.

## 0.0.35 (2026-11-12): the About window

F1 opens "About Chrono FTP" (`ui/aboutdialog.py`): the icon, the version, what the program is and the license, with Close; Enter and Escape close it (D-059). The version comes from `pyproject.toml` through `core/appinfo.py`, because the record of a source install still said 0.0.5. F1 is a program key, so a bookmark can't take it. Tests: `tests/ui/test_ui_about.py`.

## 0.0.36 (2026-11-12): the version in the title bar

The main window's title is "Chrono FTP" and the version (D-059). The question about a missing local folder took the main window's title, so it is now given the program's name. Test: `tests/ui/test_ui_about.py::test_the_title_bar_shows_the_version`.

## 0.0.37 (2026-11-12): block FM, Properties and server actions

Properties (F9) works on both sides and on several files (`ui/propertiesdialog.py`, `engine/properties.py`), with the rights editor of `ui/rightseditor.py` and `engine/rights.py`: boxes with "leave as it is" where the files differ, an octal field and Copy. On the server a change is one `chown` and one `chmod` of exactly the boxes set (`u+rw,u-x,…`, never `=`), with `-R` and `X` for folders that read, shown and confirmed first; without a shell the protocol sets each file's own rights; owner and group come from `getent` or the listing, and a typed name is checked. On this computer it runs on a thread of its own and never follows a link (D-060). The New Folder window can set rights. Link… (Alt+F6) makes and re-targets links on both sides; Duplicate… and Copy To… (Shift+F5) copy on the server with `cp`, SFTP copy-data or FTP SITE CPFR and CPTO, detected, and are greyed with their reason elsewhere; the copy is no longer a shell-only action (D-061, D-027 changed). Install My Public Key and Change Password in the menu of a tab (`engine/keyinstall.py`, `connections/passwordchange.py`, `SshConnection.change_password`); no password is ever shown or logged, and the stored one is replaced (D-062). New File (Shift+F4), Open in File Manager (Ctrl+Alt+E), and the terminal in the folder shown, which stays open where that folder can't be entered (D-063). The window part is `ui/windowproperties.py`; `ui/changedialog.py` is gone. New tests: `tests/test_server_actions.py`, `tests/ui/test_ui_properties.py`, opt-in `tests/test_live_serveractions.py` (run on this computer's SSH server); `tests/fakeservers.py` gained an SSH server whose commands the test answers (a passwd stand-in) and an FTP server with SITE CPFR and CPTO. The session, file list and queue tests follow the new names. Still open: the window sizes that come back at their defaults after a restart. Review round 1 fixed: rights for everything inside go through `find`, files first and folders deepest first, never `X`, which gave Run to files; this computer gives the same result and lists a tree whole before it changes it, reporting what it can't read; a key comment beyond ASCII is kept; a link led elsewhere is replaced in one step or put back, never lost; a server copy that fails halfway is removed; owner and group names of directories are accepted. Review round 2 fixed: a link that leads nowhere is a taken name for every copy and link, so cp's clean-up can't remove it; the temporary names of a link led elsewhere are random, and only what the call made is removed; Copy To goes on after a refusal, says what was and was not copied, and reads ~ and relative folders as the path bar; a refusal about authorized_keys is shown, never taken for a missing file; passwd that never says it is done, or a talk that breaks off, gives a calm, true message instead of "no shell". Review round 3 fixed: every action of block FM whose connection was lost while its window was open says nothing was changed; a temporary file this computer can't make (New File, key install) and a file manager that can't start are told calmly; a line lost after both new passwords says the server may have taken the new one; OK waits while the octal field holds no complete number; F9 says it reads the names and a second F9 waits; a half copy with closed folders gets its owner's rights back before it is removed, and one that stays is said. Review round 4 fixed: only the touched boxes change, also for everything inside (D-060 changed); rights without a shell go on after a refusal and say what kept its rights; the server's names get 10 seconds and then the listing's names with a note; a password that is a common word no longer garbles the server's answer; Enter in a name or the octal field moves on; texts that need widgets are made on the window's thread, and this computer's users and groups are read off it; a link change that can't tidy up says what remains. Review round 5 fixed: a date out of range shows as unknown in Properties; the name lists only suggest, any name of valid form goes; a name that is not valid UTF-8 is kept unchanged and a garbled one is never sent; without a shell and with unknown rights the window asks for a whole rights value before OK; passwd gets 60 seconds per answer. Review round 6 fixed: "Give folders Run" is off at first and gives Run only for the Read boxes switched on, and "inside" alone changes nothing; for root the current password is not asked.

## 0.0.38 (2026-11-12): block FP, program-wide

Settings got pages on the left (`ui/settingsdialog.py`): General, Panels, Transfers, Queue, Confirmations, Log, with the new choices in `config/settings.py`: three confirmations, the double-click on a file, the automatic refresh of the right side, the sort order and columns per tab, and the log's level, size and kept logs; an older file still loads (D-064). The session log (`connections/sessionlog.py`, `core/logrules.py`, hooks in `ssh.py` and `ftp.py`, `ui/logwindow.py`) writes per connection to the state folder in three levels and never a password, key or FTP's PASS line (D-065). Server Information (`engine/serverinfo.py`, `server_info` and `free_space` of the connections, `ui/serverinfodialog.py`) with free space through statvfs@openssh.com, `df` or AVBL (D-066). Clean Up Stored Data (`engine/cleanup.py`, `ui/cleanupdialog.py`), also the terminal's colour schemes (D-067). The About window shows the licence and checks GitHub on request (`engine/updatecheck.py`, `core/appinfo.py`, D-068). The window part is `ui/windowprogram.py`. New tests: `tests/test_program.py`, `tests/ui/test_ui_program.py`, opt-in `tests/test_live_serverinfo.py` (run on this computer's SSH server); `tests/conftest.py` gives every test a state folder of its own and watches the real one. Review round 1 fixed: a private key over several lines of output is cleaned whole; with the log off nothing is computed; the login never fails on the log; a zero block size gives unknown space; passwords are removed before the connection list, which stays when the store can't be reached; the automatic refresh redraws only on a change and says a failure once; closed log windows are freed; Server Information starts afresh on each open; a double-click download on a dropped tab says why. Review round 2 found nothing real. A later review fixed: the automatic refresh writes one summary line to the log, its listing only at Debug; it waits while a drag runs from or onto either side; the Log window takes new lines in batches. Still open: the window sizes that come back at their defaults after a restart.

## 0.0.39 (2026-11-13): block FQ, looks

File colours by mask (`core/masks.py`, `core/filecolours.py`, `ui/colourrules.py`, the Colours page of Settings): the first rule that fits colours a row's text and background on both sides, the selection drawn over it; on the right only while the connection is live, the connection's colour staying the rest (D-069). Thumbnails (`engine/thumbnails.py`, `ui/thumbnails.py`, `read_file` of the SSH and FTP connections, `Worker.idle` and `others_waiting`): a Thumbnails button per side and per tab, the file list with tall rows, the files in view fetched into memory one at a time, giving way to browsing, transfers and every request of the person; limit and mask on the Panels page (D-070). Toolbars (`ui/toolbars.py`): one right-click menu on every toolbar and the top row, to hide toolbars, choose small, medium or large icons, text under a chosen button and the standard layout, kept in `settings.json` at once (D-071). The window part is `ui/windowlooks.py`. New tests: `tests/test_looks.py`, `tests/ui/test_ui_looks.py`, opt-in `tests/ui/test_ui_live_looks.py` (run on this computer's SSH server); the settings page test follows the new Colours page. Still open: the window sizes that come back at their defaults after a restart.

## 0.0.40 (2026-11-13): block FG, selection and keys

Type-ahead in the file list (`ui/filelist.py`, `engine/listing.py`): the first name from the cursor on that starts with what was typed, Tab and Shift+Tab to the next and previous, Backspace, Escape or a 1.5-second pause, a beep where nothing fits, the typed text in the side's status line (D-072). Selection keys and a Select submenu: Ctrl+A, Ctrl+I, Ctrl+Shift+A, Select the Same Extension, Restore the Previous Selection, Insert; the two-pane selection mode (`selectionCommand`, `acting_entries`, Space, a drag of the focused file); This File Only for upload or download, Edit, Delete and Properties (D-073). The key set (`ui/windowselection.py`): F6 as a move to the other side, Shift+F6 on the server, Ctrl+F3 to Ctrl+F9, Alt+Left and Alt+Right, Ctrl+R, Ctrl+Shift+C, Ctrl+Alt+H, Find Files on Alt+F7 or Ctrl+F by the key set; all in PROGRAM_KEYS (D-074). Size formats, natural sort with folders first by name, sorting by extension, the column header menu (D-075). The status line of each side with selection totals and clickable hidden and filtered counts (`SideStatus`, D-076). The drop-down of Back and Forward with several steps at once, and of the path field (D-077). Clipboard copies and the path in the title bar (D-078). The right-button drag with Copy Here, Move Here and Cancel, the right-click menu waiting for the button to come up; left-button drag and drop unchanged (D-079). Pause Automatic Refresh and Clear Cached Listings in the menu of a tab (D-080). The transfer window can start with Move ticked, and a move without that window always asks first. New tests: `tests/test_listing.py`, `tests/ui/test_ui_selection.py`; the count line, a free bookmark F-key and the tab menu tests follow the changes. Still open: a real right-button drag on the desktop tried by hand; the window sizes that come back at their defaults after a restart.

## 0.0.41 (2026-11-13): block FD, masks

One mask engine (`core/masks.py`): include and exclude around a `|`, folder patterns, patterns with a path, size and date conditions with absolute and relative dates, calm faults with their place; the file colours and thumbnails keep their meaning (D-081). Masks in transfers (`transfers/conversion.py`, `transfers/filebyfile.py`, `transfers/queue.py`): a file mask, skip hidden files and skip empty folders, neither counted nor sent and told; rsync gets the exact list of what passes after a look-over (D-087). Text mode: binary, text or automatic, the line endings converted on this computer into a file of our own, the size checked against the converted copy, never continued, split or appended, file by file beside rsync (D-082). Name conversion: case and characters other systems can't hold, renamed items file by file beside rsync (D-083). Presets (`core/presets.py`, `ui/presetseditor.py`, the Presets page, the transfer window) with rules on host, user and folders (D-084). Select and unselect by mask on Num+ and Num− (D-085); the filter window with history and Clear, its mark next to the path, per tab for both sides (`ui/maskfield.py`, `ui/maskdialog.py`, `ui/pathbar.py`, D-086). Edit's saves and Compare's rows go exactly as they are (`plain`). New tests: `tests/test_masks.py`, `tests/test_transfer_masks.py`, `tests/ui/test_ui_masks.py`, opt-in `tests/test_live_masks.py` (run on this computer's SSH server); the settings page and Select submenu tests follow the new Presets page and items. Still open: the known limits in `STATUS.md`; a real Num+ on a keypad tried by hand. Review round 1: a retry or resume compares a converted text with the size it really has at the target; `~` in a preset folder rule is the home folder of its side, and a rule ending in `/*` fits that folder itself; the number pad's + and − never start finding a name and say why where greyed; Select by Mask with only a part after the `|` chooses every file but that; a fault in the greyed text mask no longer holds OK back. Review round 2: two items of one transfer whose names become one never write over each other (the later is left out, its source kept, also on a move and beside rsync); the converted copy of an upload is 0600 in a 0700 folder; mask fields never complete from the history. A fault since 0.0.12 found while making a test steady: after a broken-off FTP upload the program kept the part, and said "what arrived is kept", while the server had not yet taken in any of it; a part is now kept and told only where the server holds something of it (`FileWay._part_holds_bytes`), and the test of a source that can't be read here allows the other true outcome. Review round 3: the items the person picked always go, the mask deciding only inside a picked folder (D-081, D-087); with Folders too what a mask leaves out leaves folders out too (D-085); the converted copy of a fetched text is noted as ours and a leftover one is tidied the next time (D-082).

## 0.0.42 (2026-11-13): block FE, hashes and sizes without a shell

One checksum function (`engine/checksums.py`) takes the best way a connection offers, detected: the shell's `md5sum` to `sha512sum` in one command line for many files, each file read through standard input so no name comes back; SFTP's check-file where the hello names it (`ssh.py` keeps the hello's extension names, which asyncssh drops); FTP's HASH with OPTS HASH, or XSHA256 and the like (`ftp.py`); every answer checked, another algorithm refused; files on this computer read in pieces (D-088). Compare Contents in the Synchronize window runs the checksum step through it, so it works without a shell where the server computes checksums; same content leaves a row out, other content goes to the later side (D-027 changed for this step, D-088). Properties has a Size tab (`engine/sizes.py`: one `find -printf` with `awk` on the server for all folders where the shell has them, else the listings; links never followed; "at least" where something can't be read) and a Checksum tab (the algorithm last chosen, the files inside folders, Copy as the `*sum` tools write) on both sides (D-089). Shift+Space, or Calculate Folder Sizes in the menu of a list, counts the selected folders into the Size column on a thread of its own per side, stoppable from the status bar; Shift+Space never types or marks (`ui/windowsizes.py`, `ui/filelist.py`, D-090). A long command on the server ends when stopped (`run_stoppable`). Server Information names the checksums a server computes. New tests: `tests/test_hashes.py`, `tests/ui/test_ui_hashes.py`, opt-in `tests/test_live_hashes.py` (run on this computer's SSH server); `tests/fakeservers.py` gained an SFTP server with check-file and an FTP server with HASH and the older commands.

Review round 1 fixed: checksums through the protocol run on an SFTP channel of their own over the same login (`ssh.SftpChannel`) or an FTP login of their own, so the tab never waits, and a stop ends them at once (the FTP control connection is shut down, the next request logs in again); where the FTP server takes no further login the window says calmly that the tab waits; each calculation of Properties and Compare runs on a thread of its own and stops when the main window closes; a count belongs to its tab and folder and stops silently when that tab closes or another shows; while a name is typed Shift+Space types a space; a file the server may not read says so calmly, without the shell's words; answers are split on line ends only (D-088 to D-090 changed).

Review round 2 fixed: a stop ends the whole process group of a command on the server (its shell tells its number first), proven live on this computer; a stopped Compare Contents says so; a checksum channel closed after its connection leaves no coroutine behind; a name with a space is sent quoted after HASH, as the reference program does. Review round 3 fixed: the group is ended only where the command's shell printed itself as its leader, a plain number above 1, in the `kill -TERM -N` form dash, bash and busybox take.

Still open: a check-file or HASH the server started runs on there until the server notices its channel or login closed; D-020 still says the checksum step needs a shell; a real Shift+Space on the desktop tried by hand.

## 0.0.43 (2026-11-13): block FF, synchronize

The Synchronize window became the full synchronize of the reference program. It has a direction (to the server, to this computer, both ways) and a mode: Synchronize copies what is new or newer, Mirror makes the target an exact copy, Timestamps only gives files of the same size the source's date and is greyed with its reason where the server can't set one (D-091). Two files are compared by date (with size), by size, by checksum (the function of block FE, greyed with its reason elsewhere) or by name only, with a choice for upper and lower case; dates are compared at the step the listing reports them in, found from every date the scan saw on the other side, in place of the fixed two-second margin (D-092). A scan can be limited to existing files, to no subfolders and to the selected files, and the mask engine of block FD decides what it looks at, with "Skip empty folders" (D-093).

Below the choices is a checklist of every planned action: a tick, the action, and the size and date of both sides; it sorts by any column, Select ticks everything, nothing or one action, and the line under it says the count and total size per action of what is ticked. Reverse turns the selected rows around, and a delete turns into the copy that keeps the file. A folder that is on one side only has its size counted in the background with the counting of block FE. A file only on the target plus a file of exactly the same size only on the source becomes one row "Move on the server", ticked only where the dates or the checksums match too (D-096). D-038 changed: Synchronize may now delete. A delete row is always shown, "Delete on the other side" decides only whether it starts ticked and is greyed with its reason where it can have no meaning, and Apply names how many items will be deleted, says it can't be undone and asks first (D-094).

Apply goes through the queue: the copies are transfers as any other, and the deletes, the moves on one side and the dates to set are queue items of their own, one per kind and per side, which can be paused, stopped and watched like a transfer; one step that is refused never stops the rest, and the item ends by saying how much was done (`transfers/sidework.py`, D-095). Every choice is kept for next time, and the direction starts from the side that has the keyboard focus; the progress bar of a scan uses the file count of the last scan of the same folder pair. The menu of each list gained Mark Differences, which marks the files of the folder shown that differ from, or are missing on, the other side and copies nothing; the left list's menu also gained "Synchronize with another folder on this computer…", which opens the same window for two local folders and never touches the right side (OR-1).

New modules: `core/synchronizing.py`, `engine/synchronize.py`, `transfers/sidework.py`, `ui/synchronizeoptions.py`, `ui/windowsynchronize.py`; `ui/comparedialog.py` became the checklist window and `ui/windowserver.py` gave Synchronize up. New tests: `tests/test_synchronize.py`, `tests/ui/test_ui_synchronize.py`, opt-in `tests/test_live_synchronize.py`.

Review round 1 fixed: a copy between two folders on this computer replaces the file that is there, as a copy to a server does (the existence check was meant for a move only, so every copy over a changed file failed with a message about a move); the question before a delete names the files and the folders apart, says that a folder is deleted with everything in it, names the files inside where the background count knew them and says plainly where it did not, and names the mask too, so it never says less than it does (`engine/synchronize.delete_warning`, D-094); Reverse of a delete row copies from the side the file is still on, not the side it would be deleted from, so its copy has a source (the earlier test asserted the wrong side); Reverse has no meaning for a move row, so it is greyed with that reason (D-096); a scan can be stopped from the same button that starts it, leaves the rows as they were, leaves nothing running and says so calmly (D-093).

Review round 2 fixed: "Synchronize with another folder on this computer" refuses two folders where one lies inside the other, in either direction and after links are resolved, when they are chosen, so a copy can never write a folder into itself and a mirror can never delete the second folder (`engine/synchronize.why_not_these_two`); the checklist's count per action reads a move's size from the side its file is actually on, so "Move on the server" no longer shows 0 bytes; a background folder count started from the Synchronize window ends when that window closes, not only when the main window does, so no count goes on working on a server where nobody can see or stop it.

Still open: Apply for two folders on this computer runs on a thread of its own instead of the queue, as a queue item needs a connection; `engine/compare.py` is still there for the checksum step of block FE but no longer used by the window; the opt-in live tests of block FF have not been run.

## 0.0.44 (2026-11-13): block FF after the owner's review

The owner read the Synchronize window instead of the changelist, and it failed. "Apply" meant nothing to him; a sync of two folders on this computer could not be aborted at all, because the only thing that could set its stop was the main window closing, and the window was taken away the moment the work started.

One button now does it: it reads **Start**, and while the work runs the same button reads **Abort** (D-097). A sync of two folders on this computer keeps its window on screen while it runs, with a bar and "Doing 3 of 12: <name>"; Abort and closing the window both end it after the file it is busy with, and the window then says what was done and what was not, in words. On a server Start hands the work to the queue, opens the queue panel and says in the status bar how many things went there and that they can be watched, paused and stopped there, so the screen shows where the work went. Every long-running thing in the window has a button on screen that stops it and names what it would stop: the compare button reads Compare Again, Stop Comparing while the scan runs, and Stop Counting Folders while the sizes of one-sided folders are being worked out; Compare Contents reads Stop Comparing Contents while it runs. Each says calmly afterwards what was and was not done, and nothing is left running behind a closed window.

Then every word of the window was read as a person would read it. Columns: "Action" became "What will happen", "Size here"/"Date here" became "Size on this computer"/"Date on this computer", "Size there"/"Date there" name the server or the other folder, "Difference" became "Why". A row that is only shown and never acted on says "Nothing" instead of standing empty. Buttons: "Reverse" became "Act on the Other Side" (which is what it does, for a copy and for a delete alike), "Select" became "Tick Rows", and every button has a hover text in plain words. Choices: the groups became "Which way", "What it does", "When two files count as the same", "What it looks at", "Which files"; each mode and each criterion is now a sentence with a hover text ("They hold the same bytes (both files are read through)"); "Existing files only" became "Only files that are already on both sides", "No subfolders" became "Don't look inside subfolders", "The selected files only" became "Only what is selected on this computer", "Skip empty folders" became "Leave out empty folders". In a window about two folders on this computer the direction said "To the server"; it now says "To the other folder" (OR-1). The line under the list says "12 things to do. Nothing happens until you press Start.", and Start is greyed with its reason while nothing is ticked.

New tests in `tests/ui/test_ui_synchronize.py`: the Start/Abort button with the window on screen, closing the window aborts, Start on a server opens the queue and says so, Start greyed with its reason, the folder counting stopped from its own button, and every word of the window. Each was proved to fail on the code before this round.

A further review of the words: no hover text, message or status line names "Apply" any more (the delete box's hover text still did), nor do the docstrings and module descriptions; the engine no longer writes "Move on the server" where the window names the side. An Abort pressed after the last step had already run said "Aborted: 1 of 1 were done, and 0 were not"; nothing was aborted, so it now says it finished, in the same words as a run that was never aborted. The two stops sat next to each other and both began with "Stop Comparing": the first button now reads "Compare the Folders Again" / "Stop Comparing the Folders" / "Stop Counting the Folders" and the second "Look Inside the Files" / "Stop Looking Inside the Files", so one is plainly about folders and the other plainly about files and they differ at the first word a person reads. A last sweep took out every word only a programmer uses: "walking through" became "going through", "read through" became "look inside", "the source" and "the target" became "the side it copies from" and "the side it copies to", the checksum criterion became "They hold exactly the same thing (both files are looked inside)", and the line while the files are looked inside no longer names the algorithm, which is ours to pick and not a word the person has to know.

A teardown error that came and went in the window tests was run down. `test_connect_with_new_settings_logs_in_again_in_the_same_tab` waited only until the new login was alive, but `_connected` sets `alive` and asks for the first listing in one and the same step, so the test went on with that listing still in flight; its `with ftp_server(...)` block then stopped the fake server under that request, the window said calmly "The connection to the server was lost", and the test plugin's rule that no box may go unanswered failed at teardown, which pytest reports as an error on a test that passed. A probe that ran the body thirty times had work still in flight on 7 of 30 tries, which is why it only showed now and then. The cause is in the tests, not in the program: the window is right to say the connection was lost. `settled_tab` in `tests/ui/test_ui_sessions.py` now waits for a tab to be alive, not connecting and its worker idle, and `connected` and every reconnect in `test_ui_queue.py` and `test_ui_navigation.py` use it; the probe then had work in flight on 0 of 30 tries (AGENTS.md 4.4.4).

## 0.0.45 (2026-11-13): block FI, tabs and workspaces

Tabs: dragged to another place with that order kept per connection (`config/settings.py`, `tab_order`), the middle mouse button closing a tab (Qt's own, now held to by a test), files dropped on a tab with the border that marks the tab that would take them and the reason where one can't, Ctrl+Tab, Ctrl+Shift+Tab and Alt+1 to Alt+9, Rename Tab (kept with the connection), Duplicate Tab, Reconnect, Disconnect and Close Tab in the menu of a tab, the All Tabs button, and the folder a tab names next to its name (D-098). Workspaces in `config/workspaces.py` and `ui/windowworkspaces.py`, from the menu an empty part of the tab row opens, one of them opening by itself at start (D-100). Copy and paste between tabs in `ui/windowpaste.py`: upload, download, the server's own copy within one server, and the way through this computer between two servers as two queue items; a pasted path opens that folder (D-099). Files of this computer pasted into a folder here are copied by Chrono FTP itself, in `ui/localcopydialog.py`, with the copying of `transfers/sidework.py`, the plan of the new `engine/copyplan.py` and the one Start/Abort button of D-097, which was pulled out of the Synchronize window into `ui/startabort.py` so there is one of it. Review round 1 (seven faults): a file pasted onto a folder of its name was written INTO that folder by shutil.copy2 and silently replaced a file inside it, so the plan now refuses a file onto a folder and a folder onto a file; the paste within one server got the same window with Start, Abort and progress (`ui/copydialog.py`, one window for both sides) instead of a silent run; both sides now make a "(copy)" name for a paste into the folder the things came from; Paste is greyed unless the clipboard holds our files or a path that side can really go to, and Ctrl+V on a greyed one says why in the status bar; the window names the file it is busy with from the moment it starts and names everything that could not be copied with its reason; it says before Start which items get a copy name; and the workspaces now count what really happened, refuse a name longer than the file keeps, and fall back on the connections window when a workspace at start opens nothing. Two faults found while building: the middle mouse button already closes a closable tab in Qt, so the handler written for it closed two tabs at once and was removed; and the connections window, which has no field for a tab name, would have written an empty one back over a renamed tab. Still open: a copy of files from this computer to this computer (it would need its own Start and Abort window), and everything of this block tried by hand on the desktop; the known limits are in `STATUS.md`. Review round 2 (four faults): `copy_on_server(replace=True)` cleaned up with `rm -rf target`, which on a failed `cp` deleted the folder the person had agreed to replace (proved on a throwaway tree: a folder with `precious` in it was gone), and `cp -a dir existing_dir` nested the copy instead of replacing it; a copy that replaces something is now made as `target.chrono-ftp-new`, the old one is moved aside, the copy takes the name and only then is the old one removed, with the same discipline on this computer (`engine/copyplan.put_in_place`); a paste on a server that loses its connection now ends its window and says what was copied, instead of leaving a live Abort that did nothing; a workspace opens the two tabs the person made with Duplicate Tab (`open_another_tab`), so nothing false is said and no tab is lost; and a tab that can't take dropped files says why while they hang over it and when they are let go. Review round 3 (two faults about what the person is told): one sentence served several different failures of a replacing copy, so each step now says what really failed and that what the person had is untouched; and where the copy could not be put in place AND what was there could not be put back, a raw `OSError` reached the person, which is now a sentence naming the `.chrono-ftp-old` name their own data is safe under (OR-23). A copy cut short earlier now says where their file is and to rename it back; that state is a known limit in `STATUS.md`. Review round 4 (the English of sentences with a number): `ui/startabort.py` now builds the end sentences itself (`finished_text`, `aborted_text`) so one thing reads "Finished: the one thing was done.", one of several reads "1 of 2 was done", and an abort before anything ran says so plainly; the copy window says "This could not be" for one and "These" for more; the Synchronize window's line after looking inside the files, its folder counts and Mark Differences read properly for one file and for none; `ui/plaintext.py` gained `counted` and `was`, which every such sentence goes through.

## 0.0.46 (2026-11-13): the folder tree is sorted

The folder tree of the left side showed its folders in the order the file system handed them over, which is not always by name, so
the tree could stand in a jumbled order. Found while running down a test that failed about one run in five
(`tests/ui/test_ui_mainwindow.py::test_the_arrow_keys_pass_through_a_closed_folder_in_the_tree`): the test walked the tree with the
arrow keys and landed on another folder when the order came back reversed. `src/chrono_ftp/ui/localside.py` now sorts the tree by
name, the order the file list beside it already uses. After the fix that test passed fifteen runs in a row.

Still open: `test_another_folder_renamed_to_the_same_name_is_read_anew` in the same file fails about two runs in fifteen while the
machine is busy, and passed fifteen of fifteen on its own. It waits five seconds for the file system to report a rename, so the
deadline is what it depends on; nobody using the program meets this.

## 0.0.47 (2026-11-14): block FJ, the connection list

The list became a tree: a connection whose name holds a slash stands in a folder of that name, folders hold folders, and the saved
file keeps exactly what it kept before. Dragging a connection onto a folder, or out of every folder, goes through the one rename the
connections window already had, so its password, bookmarks and location profiles come along; the open folders are kept with the other
settings, and a folder a connection just moved into stands open. A folder's menu opens every connection in it, in the order the tree
shows them. Each connection has a note of its own, shown under the mouse in the list, and the menu of a connection can make its
settings the ones a new connection starts with, or put that back to empty. Typing letters jumps to a connection, with what was typed
shown under the list and the folder of a match opened. Import gained a second way in: this computer's own `~/.ssh/config`, with every
server found offered with a tick and every server that cannot come over whole unticked and saying why in plain words (ProxyJump,
ProxyCommand, a signed certificate, a key on a card; a Match section or an Include line refuses every server of that file). The host
keys of `~/.ssh/known_hosts` already counted as trusted since 0.0.5; a test now holds that, and that nothing in `~/.ssh` is ever
written. Open an Address… opens a pasted address as a tab that is saved nowhere, its password used for that one login only, and Copy
The Address of a connection and of a file on the server puts the address on the clipboard and shows it first, never with a password in
it. Reading an address and making one live in `core/addresses.py`, the SSH settings in `core/sshsettings.py`, the tree in
`ui/connectiontree.py`, and the two new windows in `ui/addressdialog.py` and `ui/sshimportdialog.py`; all five are tested without a
screen or through the window's own events (D-101 to D-103).

Still open: a real drag of a connection with the mouse, and the two address items, have not been tried by hand on the desktop; a
folder exists only as long as a connection stands in it; the import reads `~/.ssh/config` itself only.

After the first review round of block FJ (17 findings, all fixed in the same version): the move of a connection carries the password
first and moves nothing when it can't, and it also carries the connection the window starts on, the remembered tab order and every
workspace; a taken name in a folder says so in its own words; a name with an empty part is refused and the spaces around each part are
trimmed. An address accepts only a plain server name or a server number in brackets and refuses a user name with a space or a control
character, so nothing of an address can reach a command as an option; it always opens a tab of its own, and `may_keep` in
`ui/session.py` is now the one question every writer asks before anything is written under a tab's name, which also closed the password
write of `ui/windowproperties.py`. Copy the Address leaves out a starting folder that is no path from the root. The SSH import works
out ~, $HOME, %d, %u and %h, refuses a server whose setting holds a token it can't work out or whose port is no port, reads "none" as
"not in use", turns a slash in a name into a space, and says apart whether there are no settings, no server or settings that could not
be read; Add says it when no server at all can come over. Every new name follows the program's own style, and `ARCHITECTURE.md` has a
module map for the block.

After the second review round of block FJ (10 findings, all fixed in the same version): a copy of a tab that may not be kept may not
be kept either (`duplicate_tab` and `open_another_tab`), and every action that would write under a tab's name now asks `may_keep` and
is greyed with its reason — Add Bookmark, Add the Current Folders…, Organize Bookmarks and Organize Location Profiles — while a typed
path is simply not kept for such a tab. The move of a connection writes the list before it removes the old password, so a list that
can't be saved moves nothing and the password stays where the connection is. One place now reads a server name in a pasted address:
an international name is worked out to the form the network uses, underscores are ordinary, every part and the whole name are within
their length, the scope after a server number is checked, only ASCII digits are a port, a control character in the folder is refused,
and every cause says its own thing; the user name and the password end at the last @, so a password with a slash in it is read as one.
Typing in the connection list can type a space. In the SSH settings a # starts a comment, as OpenSSH reads it, which also stopped the
words of a comment on a Host line becoming servers of their own, and `IdentityFile none` means no key file.

After the third review round of block FJ (12 findings, all fixed in the same version): a port written with a digit of another language
crashed the reading of the whole settings file, because isdigit is true for such a digit and int cannot read it; only ASCII digits are
a port now, and one server whose settings cannot be read refuses only itself. %h is the server the connection really uses; a setting
too long to read and a name that is no server name refuse their server, the name read by the one place a pasted address uses; a
backslash before a # keeps it. Every refusal is in the person's own words and says what they can do. The import says its three
"nothing was added" cases apart and adds nothing when the list cannot be written. In an address every cause says its own thing, and a
network address goes between square brackets wherever a login is built (`core/addresses.login_text`, used by `transfers/rsyncway.py`),
so a tab from such an address can transfer too. A move asks the connection list, the settings, the workspaces and the bookmarks
whether they can be written before anything is renamed (`config/connections.can_write`), and no write in that chain is swallowed. Every
lookup of an open tab by name skips a tab opened from an address.

After the fourth review round of block FJ (12 findings, all fixed in the same version): an unquoted value of more than one word, and a
value with a character that can't be in it, refuse their server instead of becoming the first word or reaching the login; a bare
network address is a server name in the settings, where no port can follow it; the settings are read once for every server, so a file
of 500 servers and 20,000 comment lines takes 0.13 s where it took 2.4 s. Every refusal names its cause from the one place that reads
a server name, keeps what it echoes short, stands once, and the three a connection by hand cannot mend say what is true. Reconnect,
and the bookmark and profile menus, no longer take a saved connection for a tab opened from an address. The check before a move writes
real bytes and flushes them; the stores that follow a name are written before the connection list and put back when one fails; a typed
password that cannot be kept saves nothing; every sentence about a move that did not happen names the file and says what to do. The
import window's columns are Name and Server.

After the fifth review round of block FJ (14 findings, all fixed in the same version): a setting above the first Host line counts for
every server; every message about a move or a rename that did not go through says what is true, in the words of what the person did,
and names the file that stood in the way; putting a store back writes what stood there before, so no left-over is dragged along, and a
putting back that fails says what is on the disk. Reconnect, Edit in the tab menu, the tab name, the password read from the store, the
Open folder window and the Server Information window ask `may_keep` too. In a pasted address an @ in a folder is a folder, a network
address without brackets and anything but a port after the brackets each say what to do, and the character that makes a name no name is
named. The SSH refusals drop every word a person would not use, name the short-hand or the character they found, and the port refusal
offers the Port row. The sentences about the password store, the bookmarks and a copy left behind say what the person can do. Numbers
read right for one as well as for many.

After the sixth review round of block FJ (15 findings, all fixed in the same version): the paired sentences about a save or a move that
half failed are gone. Three sentences built from facts took their place — nothing changed, something is left under the other name, a
copy of a password is left — each under 25 words, with no advice that can be wrong and nothing that reads untrue when a name is empty.
`rename_stores` now hands back facts and a way to put every store back exactly as it stood, so the old swapped-name undo is gone. A
deleted connection leaves nothing in the order of the tabs and a rename never doubles a name there. An address is read one way only, so
a password with a slash is written %2F; a port given twice says so. In the SSH settings a setting above the first Host line counts for
every server, a network address with a scope is no short-hand, brackets there say to drop them, ~ alone is the person's own home and %%
is one percent sign, and the three refusals Chrono FTP can't mend are one line each with no terminal in them. The mark of a connection
that is in no list moved onto the connection itself, which made the log, the log window and the place in the row of tabs follow.

After the seventh review round of block FJ (9 findings, all fixed in the same version): a tab opened from an address counts as no tab
of a connection that carries the name it was given, so a workspace of that connection opens its own tabs; a deleted connection also
leaves no connection the window starts on; a sentence about a half-failed move names every file that could not be saved and says why
it stopped first; a new connection reads that it was not saved, not that it is as it was; the password store says which call failed;
the leftover-copy box comes after the explanation; a name after Host that is no server name says that; no refusal promises a version
and the import window says "added"; and a slash in a password says how to write it.

After the eighth review round of block FJ (5 findings, all fixed in the same version): an address whose first part can be read as a
server with a port or as a user name with a password is read as neither and says so, which replaced the guess that called a mistyped
port a password; deleting the connection New starts from clears that too; and deleting or copying a connection while the list cannot be
written now changes nothing and says so, where it crashed before (a fix beyond the changelist, OR-15). The refusal of a name that is no
server name lost the SSH keyword and the last "yet" in the block is gone.

After the ninth review round of block FJ (2 findings, both fixed in the same version): the refusal of round 8 is gone again. It turned
away ordinary addresses — `sftp://host:22/reports/bob@mail.txt` and the very address Copy the Address hands out for a connection without
a user name — so the program refused what it had just given the person. An address is now read the one way addresses are built: the user
name and its password before the last @ before the first slash, the port after the colon of the server part, so `sftp://user:12/ss@server/x`
names the server `user` on port 12, which is what it says. Instead of guessing, the window tells the truth at the moment the person acts:
`core/addresses.understood` puts what was read in plain words and it stands under the field in Open an Address… while they type, with the
refusal sentence in the same place and Open greyed with it as its reason. Every sentence the earlier rounds settled for a bad port, a bad
name and the brackets is unchanged. The greyed Export… button says what is true today in place of a promise of a later version.

After the tenth review round of block FJ (2 fixes): the help line asks only for %2F in a password, because an @ in a folder needs no
writing out; and where no user name was read while the folder holds an @, the window says that too, under the server and the port, so a
password someone wrote with a plain slash stands out where the eye lands. Open stays enabled: the address is proper and may be meant.
