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
