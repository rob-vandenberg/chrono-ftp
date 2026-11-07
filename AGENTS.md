# AGENTS.md: guide for AI agents and contributors

Chrono FTP is an open-source desktop file transfer program for Linux, written in Python with a Qt 6 user interface through PySide6. It is a two-pane remote file manager: the left side is always this computer, the right side is always a server, and every connection has its own colour.

## 0. The basic rule

The person must always see where they are, and the program must never damage a server (OR-1 to OR-7):

1. Left is always local, right is always remote.
2. A colour on the right always means a live connection, and tells which one.
3. Work on a server is done on the server: one command where a shell exists, nothing travels through this computer.
4. What a connection cannot do is greyed out, and never fails halfway.
5. A recursive change of owner, group or permissions shows its exact command and waits for a yes.

Every changelist says how it keeps this rule. What the rule does not settle is asked, never chosen.

## 1. Orientation

| File | Purpose | When to read |
|---|---|---|
| `AGENTS.md` | Project-specific technical rules (this file) | Loaded by itself |
| `OWNER-RULES.md` | Every rule the owner set for this project, numbered (OR-nn); only the owner changes it; private, not in git | Loaded by itself |
| `STATUS.md` | Where we are now, the next task, open questions | Every session, first |
| `TODO.md` | The short-term to-do list; private, not in git | Every session, after `STATUS.md` |
| `DECISIONS.md` | The entry point to every design decision: `decisions/D-nnn.md` is one short file per decision | Every session, after `STATUS.md`, in full; then only the topics your task needs |
| `decision-details/` | Per decision `D-nnn-details.md`: its full wording and every older wording; private, not in git | Only when a short version is not enough |
| `ARCHITECTURE.md` | Module map, layers, connection layer | Before any code task |
| `ROADMAP.md` | Milestones and tasks, each with a status | When picking or finishing a task |
| `USER-REQUESTS.md` | The backlog of requests from people who use Chrono FTP (UR-numbers) | When picking a task |
| `DEVLOG.md` | Append-only log, one entry per completed task | When you need history |

## 2. Naming

| Context | Form |
|---|---|
| Project name, user-facing text | **Chrono FTP** |
| Project identifier, repository name, release file names | `chrono-ftp` |
| Where hyphens are not allowed (Python package, module names) | `chrono_ftp` |
| Release and delivery file names (PY-1) | `chrono-ftp_x.y.z.ext` |

## 3. Technology (fixed, see DECISIONS.md)

1. **Language:** Python 3.14, virtual environment in `.venv`.
2. **Type checking:** Pyrefly in **strict** mode. Zero type errors is a hard gate. Every function, method and module-level variable carries full type annotations. No `Any` unless unavoidable and commented.
3. **GUI:** Qt 6 through **PySide6**. No KDE Frameworks, no GTK, no webview shells.
4. **License:** AGPL-3.0. Every dependency must be license-compatible.

## 4. Golden rules

### 4.1 Never damage a server

1. A recursive change shows its exact command and needs a yes first (OR-6).
2. Every path in a shell command goes through `shlex.quote`.
3. Capabilities are detected, never assumed: the system, the tools and their options, what the account may do (D-016).
4. Everything that changes a server is tested on a throwaway folder first, never first on a live server (OR-26).

### 4.2 Never crash on input

Servers are hostile input: odd file names, broken listings, slow or dropped connections.

1. A bad listing, file name or reply produces an error for that item only, never a crash and never a stalled transfer.
2. Numbers that come from input (sizes, counts, dates) are validated before use.
3. Network errors, timeouts and permission errors are normal events and handled as such.
4. Every crash fix comes with a regression test built from the input that caused it.

### 4.3 Everything is a command

Every user-visible action is a command in the engine, with an id, parameters and tests. The UI contains no business logic. So an AI agent can drive and test every feature without a screen.

### 4.4 Tests are the gate

1. Every change comes with tests.
2. A task is only done when the tests related to the change pass (never the full suite, the owner's rule), the type check of §3 passes and the layer check passes.
3. Connection code is tested against real servers through opt-in `live` tests (`pytest -m live`). The default run (`pytest`) stays offline and excludes them.
4. **A test is unambiguous.** Its result never depends on what ran before it, nor on timing:
   1. It waits for the thing it checks, never for something that usually comes first.
   2. It answers every message box and window it causes; nothing ever waits for a click.
   3. It leaves nothing behind.
   4. Its name is the same in every run.
   5. No test takes longer than 20 seconds (`tests/conftest.py` stops it and prints where it waited).

### 4.5 Security and privacy

1. Passwords and keys are stored in the operating system's credential store (`keyring`), never in plain-text files.
2. TLS and host key verification are on by default; the person may choose otherwise, never the program on its own.
3. Never log passwords or keys.

## 5. Code conventions

1. Versions, the project version in `pyproject.toml` and the version block of every file: PY-1 to PY-3.
2. A change of only documents keeps the version and is pushed with `./gitpush.sh` without a message (OR-27).
3. Small modules with a single clear responsibility.
4. Standard library first, well-maintained packages second, own code last.
5. Names are descriptive and spelled out in full.

## 6. Workflow with AI agents

The owner's rules apply in full: the general rules (G-nn), the Python rules (PY-nn) and `OWNER-RULES.md` (OR-nn).

1. **Within an approved changelist** (G-4), the agent may run tests and fix its own test failures, as long as the fixes stay inside the approved scope.
2. **Delivery.** Files are edited in place. When a job is done and reviewed, the agent pushes it with `./gitpush.sh` and names the pushed version (OR-27). The agent never runs `build-release.sh` (OR-30).
3. **Checks.** Run Pyrefly also with `--python-platform win32`. Prove that a new test fails on the previous version of the code.
4. **Cautions.** Never use `pkill -f`. Never repeat or commit a password.
5. **Scripts meant for later** go in the project's `scratchpad/` folder (private, never in git).

## 7. Before you finish a task

1. The task passes the gate of 4.4.2.
2. `ROADMAP.md`: update the task's status.
3. `STATUS.md`: rewrite it (current state, next task, open questions).
4. `DEVLOG.md`: append a short entry (what landed, what is still open).
5. `DECISIONS.md`: for every design decision made during the task, a new or changed decision file, as the start of `DECISIONS.md` says.

Sessions can end abruptly. The documents plus a working tree are how the next session picks up. Keep them current after every task.
