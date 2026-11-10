"""Text that holds names is shown exactly as written: a file named a<b>x</b>.txt or &amp; is never read as HTML."""

import html

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QLabel, QMessageBox, QWidget

# --- Version ------------------------------------------------------------
__version__ = "plaintext.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: Initial: labels in plain text; message box texts that Qt shows exactly as written; long names fitted to the screen; long
#          words in a sentence shortened; a path shown whole over several lines; a question or message with details to copy, gone after
#          use

WHOLE_TEXT_START: str = "<p style='white-space: pre-wrap'>"
WHOLE_TEXT_END: str = "</p>"
NAME_SHARE: float = 0.4  # a name takes at most this part of the screen's width: a window with it stays on the screen
FALLBACK_WIDTH: int = 1024  # without a screen


def plain_label(text: str = "", parent: QWidget | None = None) -> QLabel:
    label = QLabel(parent)
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setText(text)
    return label


def box_text(text: str) -> str:
    """A message box guesses whether its text is HTML and offers no plain setting in its ready-made questions. This text is HTML on
    purpose, with every character of the plain text escaped and its line breaks kept, so the box shows exactly the plain text."""
    return WHOLE_TEXT_START + html.escape(text, quote=False) + WHOLE_TEXT_END


def plain_of(shown: str) -> str:
    """The plain text a message box shows for a text made by box_text; for the tests, which read what the box was given."""
    if shown.startswith(WHOLE_TEXT_START) and shown.endswith(WHOLE_TEXT_END):
        return html.unescape(shown[len(WHOLE_TEXT_START) : -len(WHOLE_TEXT_END)])
    return shown


def fitting(text: str, widget: QWidget, share: float = NAME_SHARE) -> str:
    """A name shortened in its middle (with an ellipsis) to at most share of the screen's width, so even a name of 250 characters
    without a space keeps its window on the screen and its buttons in reach. The whole name goes in the hover text."""
    return widget.fontMetrics().elidedText(text, Qt.TextElideMode.ElideMiddle, _room(widget, share))


def _room(widget: QWidget, share: float) -> int:
    screen = QGuiApplication.primaryScreen()
    return int((screen.availableGeometry().width() if screen is not None else FALLBACK_WIDTH) * share)


def fitting_words(text: str, widget: QWidget, share: float = NAME_SHARE) -> str:
    """A sentence that wraps at its spaces as ever, but a word too long for a line (a path in a server's error) is shortened in its
    middle, so the window stays on the screen. The whole text goes in the hover text."""
    return " ".join(fitting(word, widget, share) for word in text.split(" "))


def wrapped(text: str, widget: QWidget, share: float = NAME_SHARE) -> str:
    """A path shown whole, also when it is too long for a line: it is broken over lines, after a slash where one is near, else where the
    line is full. Nothing of it is left out: where it is the only way to find a file, every character counts."""
    room = _room(widget, share)
    metrics = widget.fontMetrics()
    lines: list[str] = []
    line = ""
    for character in text:
        if line and metrics.horizontalAdvance(line + character) > room:
            cut = line.rfind("/") + 1
            if 0 < cut < len(line):  # after the last slash: the rest goes on the next line
                lines.append(line[:cut])
                line = line[cut:]
            else:
                lines.append(line)
                line = ""
        line += character
    return "\n".join([*lines, line])


def asked(parent: QWidget, title: str, text: str, detail: str = "") -> bool:
    """A Yes or No question; detail: what goes whole and unbroken in the box's details, where it can be copied (a path broken over lines
    in the text can't be). Yes is True."""
    if not detail:
        return QMessageBox.question(parent, title, box_text(text)) == QMessageBox.StandardButton.Yes
    box = QMessageBox(QMessageBox.Icon.Question, title, box_text(text), QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, parent)
    box.setDetailedText(detail)
    answer = box.exec()
    box.deleteLater()  # its parent is the window: without this every question would stay behind
    return answer == QMessageBox.StandardButton.Yes.value


def told(parent: QWidget, title: str, text: str, detail: str = "") -> None:
    """A message; detail as for asked."""
    if not detail:
        QMessageBox.information(parent, title, box_text(text))
        return
    box = QMessageBox(QMessageBox.Icon.Information, title, box_text(text), QMessageBox.StandardButton.Ok, parent)
    box.setDetailedText(detail)
    box.exec()
    box.deleteLater()
