"""The session log of one connection, shown live (FP-02): what is in its file, and every line as it is written."""

import threading
from typing import override

from PySide6.QtCore import Qt
from PySide6.QtGui import QCloseEvent, QFontDatabase
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.connections.sessionlog import SessionLog, rules
from chrono_ftp.core.logrules import OFF
from chrono_ftp.ui.bridge import Bridge
from chrono_ftp.ui.plaintext import plain_label

# --- Version ------------------------------------------------------------
__version__ = "logwindow.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Initial: the Session Log window of a tab: the end of the log's file, then each new line as it comes (FP-02); review: new
#          lines reach the window in batches, one call for all that came meanwhile, so a long listing never freezes it

TITLE: str = "Session Log: {name}"
OFF_TEXT: str = "The session log is off. You can switch it on in Settings, on the Log page."
ON_TEXT: str = "Written to {path}"
MOST_LINES: int = 20_000  # the window keeps this many; the file keeps everything up to its size
FIRST_BYTES: int = 1024 * 1024  # what is read of the file when the window opens
WIDTH: int = 900
HEIGHT: int = 500


class LogWindow(QDialog):
    """Open beside the main window; closing it ends the watching, the log itself goes on."""

    def __init__(self, name: str, log: SessionLog, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle(TITLE.format(name=shown_name(name)))
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)  # a closed window holds no lines until the program ends
        self.log: SessionLog = log
        self.bridge: Bridge = Bridge(self)  # lines come from the connection's threads
        self.waiting: list[str] = []  # lines written that the window has not shown yet
        self.waiting_lock: threading.Lock = threading.Lock()
        self.flush_asked: bool = False  # a call to show them is on its way
        self.text = QPlainTextEdit()
        self.text.setReadOnly(True)
        self.text.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.text.setMaximumBlockCount(MOST_LINES)
        self.text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        self.state = plain_label()
        self.state.setWordWrap(True)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.close)
        bottom = QHBoxLayout()
        bottom.addWidget(self.state, 1)
        bottom.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        layout.addWidget(self.text, 1)
        layout.addLayout(bottom)
        self.resize(WIDTH, HEIGHT)
        earlier = log.tail(FIRST_BYTES)
        if earlier:
            self.text.setPlainText(earlier.rstrip("\n"))
        self.show_state()
        self.log.watch(self._written)

    def show_state(self) -> None:
        """Whether the log is on, and where it goes, or why it could not be written."""
        if rules().level == OFF:
            self.state.setText(OFF_TEXT)
        else:
            self.state.setText(self.log.problem or ON_TEXT.format(path=shown_name(str(self.log.path))))

    def _written(self, lines: list[str]) -> None:
        """On the thread that wrote: the lines wait together, and one call to the window takes all that came meanwhile."""
        with self.waiting_lock:
            self.waiting.extend(lines)
            del self.waiting[:-MOST_LINES]  # more than the window keeps is never handed on
            if self.flush_asked:
                return
            self.flush_asked = True
        self.bridge.deliver(self._add)

    def _add(self) -> None:
        with self.waiting_lock:
            lines = self.waiting
            self.waiting = []
            self.flush_asked = False
        if not lines:
            return
        bar = self.text.verticalScrollBar()
        at_end = bar is None or bar.value() == bar.maximum()
        self.text.appendPlainText("\n".join(lines))  # at once; the window's own limit of lines still holds
        if at_end and bar is not None:
            bar.setValue(bar.maximum())  # follows the newest line, unless the person scrolled up to read
        if self.log.problem:
            self.show_state()

    @override
    def closeEvent(self, event: QCloseEvent) -> None:
        self.log.unwatch(self._written)
        self.bridge.close()
        super().closeEvent(event)

    @override
    def done(self, result: int) -> None:
        self.log.unwatch(self._written)  # Escape closes through done, not closeEvent
        super().done(result)
