# Contributing to Chrono FTP

Chrono FTP is an open-source two-pane file transfer program for Linux. This file is for developers. The description for people who use the program is in [`README.md`](README.md).

## Technology

Python 3.14, Qt 6 through PySide6, strict static type checking with Pyrefly. SSH connections use `asyncssh`, FTP connections use `ftplib`.

## Set up from the source

```bash
git clone https://github.com/rob-vandenberg/chrono-ftp && cd chrono-ftp
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest
```

## Where to start reading

Start with [`AGENTS.md`](AGENTS.md) and [`STATUS.md`](STATUS.md). The design is in [`ARCHITECTURE.md`](ARCHITECTURE.md) and [`DECISIONS.md`](DECISIONS.md).

## License

GNU Affero General Public License v3.0. See [`LICENSE`](LICENSE).
