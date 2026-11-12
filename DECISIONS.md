# DECISIONS.md

The design decisions of Chrono FTP, and how they are kept ([[D-024]]):

1. Every decision is one file, `decisions/D-nnn.md`: its title, its topics and its short version: what is decided and why. "D-nnn" anywhere means that file.
2. Its full wording, reasons and every older wording are in `decision-details/D-nnn-details.md`. That folder is on this computer only, never on GitHub.
3. A topic file, `decisions/<topic>.md`, lists the decisions of one subject by number and title.
4. This file holds the core decisions that bind all work, and the list of topics.

**Read only what your task needs.** Read this file in full; then open only the topics and decisions that concern your task.

**A new decision** gets the next number (next: **D-059**). Write its two files, add it to its topic files, and to the core decisions below only when it binds all work. The short version says what is decided and why, in two or three sentences. **When a decision changes,** first copy its old wording, dated, to the end of its details file, then rewrite both. A decision that no longer holds becomes "→" and the number of the decision that replaced it. References are written inside double square brackets, so Obsidian can draw the graph.

## Core decisions (they bind all work)

- [[D-001]] (the owner) **What Chrono FTP is.** A two-pane remote file manager for Linux that grows into a full counterpart of the reference program: the features of `FEATURES.md`, built block by block. Left out: what is Windows-only or breaks an owner rule. Why: the owner relies on the reference program and has none on Linux, and the first version proved the base (OR-13).
- [[D-006]] (the owner) **Left is local, right is remote.** The left side is always local and the right side always remote, and this can never be swapped (OR-1). Why: in a split view either side can be either, and a home folder looks the same on every machine.
- [[D-007]] (the owner) **Colours.** A connection can have a background colour the person chooses, on the right side only, both tree and file list; it is off for a new connection. A live connection without a colour looks like the left side. Not connected is plain grey, and a tab whose connection dropped is grey too. Text is black or white by the brightness of the background (OR-2, OR-3). Why: the folder tree is where the owner got lost.
- [[D-008]] (the owner) **Tabs.** Every tab is one connection and keeps its own local and remote folder; a click on another tab changes both sides. There is one local side, without tabs of its own. Tabs, browser and session list follow the reference program, never identical (OR-4, OR-14). Tabs take the connection's colour, inactive ones dimmed, through a small custom tab bar that runs above both sides.
- [[D-009]] **One connection layer.** One common layer with two equal implementations, SSH and FTP. Panes, tabs and colours do not know which is in use. Why: each server uses whichever it supports.
- [[D-012]] (the owner) **Recursive changes run on the server.** A recursive change of owner, group or permissions is one command on the server wherever a shell exists, never a loop from this computer (OR-5). Why: thousands of files take seconds with one command and an hour or more file by file.
- [[D-013]] (the owner) **Confirm recursive commands.** Before any recursive `chown` or `chmod` the exact command is shown and confirmed (OR-6). Why: a bug here can damage a server.
- [[D-016]] (the owner) **Capability detection.** On connecting, the program detects the system, the tools and their options, and what the account may do. Each connection reports what it can do; the rest is greyed out and never fails halfway, except the shell actions on FTP, which are not shown (OR-7, [[D-027]]). Why: the machines vary a lot.
- [[D-027]] (the owner) **Actions that need a shell.** An action that needs a shell is available only on an SFTP connection with a working shell: recursive owner, group and permissions, copy and search on the server, the terminal and the `sha256sum` step of compare. On an FTP connection these actions are not shown; on an SFTP connection without a shell they are greyed out. Where there is no shell, rename, move and delete use the protocol, which is slower. Why: a per-file loop over a whole tree takes an hour or more.
- [[D-022]] **Server work and layers.** Anything that touches a server stays outside the window code, in `connections` and `transfers`, so it can be tested without a screen; pure window actions stay in the window. Layers are enforced by import-linter and only `ui` imports PySide6 (OR-17). There is no separate command-line program, because the scope has none and end-user tasks are graphical (OR-25).
- [[D-023]] (the owner) **No personal detail on GitHub.** No computer name, server, network address or account detail in the project folder or on GitHub; neutral words in the documents, made-up data in tests. No other program's name and no name of the AI assistant reaches GitHub (OR-10, OR-11). `gitpush.sh` scans every outgoing file and the message, and the push guard checks every commit.
- [[D-024]] **How decisions are kept.** One short file per decision in `decisions/`, its full wording in `decision-details/`, and `DECISIONS.md` as the entry point. Why: every chat reads `DECISIONS.md`, so it must stay small.
- [[D-025]] (the owner) **Pushing and releases.** Pushes go through the private `./gitpush.sh`, which wraps the owner's release tool. Only the owner runs `build-release.sh` (OR-27, OR-30). Installers come only for milestones (OR-29).

## Topics

1. [[program]]: What the program is and what it is built with.
2. [[window]]: The window, tabs, colours and the connection list.
3. [[connections]]: The connection layer.
4. [[actions]]: Actions on a server.
5. [[transfers]]: Copying files.
6. [[way-of-working]]: How the project is run.
