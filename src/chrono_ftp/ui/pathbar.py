"""The clickable path above each side (FH-01): a row of folder parts, each opens its folder; the empty part, or Ctrl+L, turns it into a
field to type a path in. The path of the side with the keyboard focus is highlighted."""

from typing import override

from PySide6.QtCore import QEvent, QObject, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFocusEvent, QFont, QFontMetrics, QKeyEvent, QMouseEvent, QPainter, QPaintEvent
from PySide6.QtWidgets import QLineEdit, QSizePolicy, QStackedLayout, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.core import colours

# --- Version ------------------------------------------------------------
__version__ = "pathbar.py 0.0.34"

# --- Version history ----------------------------------------------------
# v0.0.34: Initial: the folder parts, typing a path, the highlight of the focused side, shortened from the left (FH-01); typing goes on
#          while another window or a menu has the focus; the field keeps the real path of the folder (also a name that is not valid
#          UTF-8, and never a note), a refresh leaves what is typed alone, and the side's list takes the focus when typing ends; a
#          part opens nothing while typing is not allowed; Ctrl+L while typing selects what is typed

ELLIPSIS: str = "…"
SEPARATOR: str = "/"
PADDING: int = 6  # room at both ends of the row
GAP: int = 2  # room around a separator
KEPT_TYPING: tuple[Qt.FocusReason, ...] = (Qt.FocusReason.ActiveWindowFocusReason, Qt.FocusReason.PopupFocusReason)  # another window or a menu: the typing waits


class PathParts(QWidget):
    """The folder parts of a path. A part that doesn't fit makes room from the left: the last folder always stays."""

    part_clicked = Signal(object)  # the path of the part, as a str: a name that is not valid UTF-8 stays what it is
    empty_clicked = Signal()

    def __init__(self, height: int, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.row_height: int = height  # the height of the field it changes into, so nothing below jumps
        self.parts: list[tuple[str, str]] = []  # (what is shown, the path it opens)
        self.note: str = ""  # shown after the parts, not clickable: "no access", "Not connected"
        self.focused: bool = False
        self.hovered: int = -1
        self.places: list[tuple[QRect, int]] = []  # where each shown part is, by its number in parts
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.IBeamCursor)

    @override
    def sizeHint(self) -> QSize:
        return QSize(PADDING * 2 + self.fontMetrics().horizontalAdvance(ELLIPSIS), self.row_height)

    @override
    def minimumSizeHint(self) -> QSize:
        return self.sizeHint()

    def _shown_texts(self) -> list[tuple[str, int]]:
        """What is drawn, from the left: (text, number of the part, or -1 for a separator, the ellipsis and the note)."""
        metrics = self.fontMetrics()
        room = self.width() - 2 * PADDING
        note = f"  ({self.note})" if self.note and self.parts else self.note
        pieces: list[tuple[str, int]] = []
        for number, (text, _path) in enumerate(self.parts):
            if number > 0 and not self.parts[number - 1][0].endswith(SEPARATOR):
                pieces.append((SEPARATOR, -1))
            pieces.append((text, number))
        if note:
            pieces.append((note, -1))

        def width(found: list[tuple[str, int]]) -> int:
            return sum(metrics.horizontalAdvance(text) + (2 * GAP if text == SEPARATOR else 0) for text, _number in found)

        cut = width([(ELLIPSIS, -1), (SEPARATOR, -1)])  # what stands for the parts left out
        starts = [index for index, (_text, number) in enumerate(pieces) if number >= 0]  # where each part begins
        start = 0
        for begin in starts:  # whole parts make room from the left; the last folder always stays
            start = begin
            if width(pieces[begin:]) + (cut if begin else 0) <= room or begin == starts[-1]:
                break
        shown = pieces[start:]
        if start > 0:
            shown = [(ELLIPSIS, -1), (SEPARATOR, -1), *shown]
        return shown

    def lay_out(self) -> None:
        """Where each part is now; drawing and a click both use it."""
        metrics = self.fontMetrics()
        x = PADDING
        self.places = []
        right = self.width() - PADDING
        for text, number in self._shown_texts():
            advance = metrics.horizontalAdvance(text) + (2 * GAP if text == SEPARATOR else 0)
            if number >= 0:
                self.places.append((QRect(x, 0, min(advance, max(right - x, 0)), self.height()), number))
            x += advance

    def part_at(self, place: QPoint) -> int:
        self.lay_out()
        return next((number for rect, number in self.places if rect.contains(place)), -1)

    @override
    def paintEvent(self, event: QPaintEvent) -> None:
        self.lay_out()
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(colours.PATH_FOCUSED if self.focused else colours.PATH_UNFOCUSED))
        painter.setPen(QColor(colours.ON_PATH_FOCUSED if self.focused else colours.ON_PATH_UNFOCUSED))
        metrics = self.fontMetrics()
        x = PADDING
        right = self.width() - PADDING
        for text, number in self._shown_texts():
            advance = metrics.horizontalAdvance(text) + (2 * GAP if text == SEPARATOR else 0)
            font = QFont(self.font())
            font.setUnderline(number >= 0 and number == self.hovered)
            painter.setFont(font)
            shown = QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, max(right - x, 0)) if x + advance > right else text
            painter.drawText(QRect(x + (GAP if text == SEPARATOR else 0), 0, max(right - x, 0), self.height()), int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft), shown)
            x += advance
            if x >= right:
                break
        painter.end()

    @override
    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        hovered = self.part_at(event.position().toPoint())
        if hovered != self.hovered:
            self.hovered = hovered
            self.setCursor(Qt.CursorShape.PointingHandCursor if hovered >= 0 else Qt.CursorShape.IBeamCursor)
            self.update()
        super().mouseMoveEvent(event)

    @override
    def leaveEvent(self, event: QEvent) -> None:
        self.hovered = -1
        self.update()
        super().leaveEvent(event)

    @override
    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            return
        number = self.part_at(event.position().toPoint())
        if number >= 0:
            self.part_clicked.emit(self.parts[number][1])
        else:
            self.empty_clicked.emit()


class PathBar(QWidget):
    """The path of one side: its folder parts, or, while the person types, a field. Enter goes there: the side reads typed_path() and
    calls stop_typing. Escape and a click elsewhere in the window return to the parts; the side's file list then has the focus."""

    folder_chosen = Signal(object)  # a str: the folder of the part clicked

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.line = QLineEdit()
        self.parts = PathParts(self.line.sizeHint().height())
        self.shown_text: str = ""  # what the field holds while the parts show
        self.real_path: str = ""  # the folder shown, as it is: a name that is not valid UTF-8 keeps its bytes
        self.typing_allowed: bool = True  # typing in the field and clicking a part; off while there is no live folder
        self.next_focus: QWidget | None = None  # what takes the keyboard focus when the typing ends (the side's file list)
        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(0, 0, 0, 0)
        self.stack.addWidget(self.parts)
        self.stack.addWidget(self.line)
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)  # all the height goes to the lists
        self.parts.part_clicked.connect(self._part_clicked)
        self.parts.empty_clicked.connect(self.start_typing)
        self.line.installEventFilter(self)

    @property
    def typing(self) -> bool:
        return self.stack.currentWidget() is self.line

    def show_path(self, real_path: str, parts: list[tuple[str, str]], note: str = "", text: str | None = None) -> None:
        """The folder shown now. text: what the field holds while the parts show (the path, or a note such as Not connected). While the
        person types, the field and what was typed stay as they are; the parts follow at once."""
        self.real_path = real_path
        self.parts.parts = parts
        self.parts.note = note
        self.parts.hovered = -1
        self.shown_text = shown_name(real_path) if text is None else text
        if not self.typing:
            self.line.setText(self.shown_text)
        self.parts.update()

    def set_focused(self, focused: bool) -> None:
        if focused != self.parts.focused:
            self.parts.focused = focused
            self.parts.update()

    def _part_clicked(self, path: object) -> None:
        if self.typing_allowed:  # a dropped or missing connection opens nothing
            self.folder_chosen.emit(path)

    def start_typing(self) -> None:
        """The field, holding the path of the folder shown and nothing else. Already typing (Ctrl+L again): all of it is selected, and
        what is typed stays."""
        if self.typing:
            self.line.setFocus(Qt.FocusReason.ShortcutFocusReason)
            self.line.selectAll()
            return
        if not self.typing_allowed:
            return
        self.line.setText(shown_name(self.real_path))
        self.stack.setCurrentWidget(self.line)
        self.line.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.line.selectAll()

    def typed_path(self) -> str:
        """What was typed, with the folder shown in its real form: text the person left as it was, or that goes on below it, keeps the
        bytes of a name that is not valid UTF-8."""
        text = self.line.text().strip()
        shown = shown_name(self.real_path)
        if not self.real_path or shown == self.real_path:
            return text
        if text == shown:
            return self.real_path
        if text.startswith(shown.rstrip("/") + "/"):
            return self.real_path.rstrip("/") + text[len(shown.rstrip("/")) :]
        return text

    def stop_typing(self) -> None:
        """Back to the parts; the field holds the folder shown again. When the field had the keyboard focus, the side's list takes it."""
        if not self.typing:
            self.line.setText(self.shown_text)
            return
        had_focus = self.line.hasFocus()
        self.stack.setCurrentWidget(self.parts)
        self.line.setText(self.shown_text)
        if had_focus and self.next_focus is not None:
            self.next_focus.setFocus(Qt.FocusReason.OtherFocusReason)

    @override
    def eventFilter(self, watched: QObject, event: QEvent) -> bool:
        if watched is self.line and event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent) and event.key() == Qt.Key.Key_Escape:
            self.stop_typing()
            return True
        if watched is self.line and event.type() == QEvent.Type.FocusOut and isinstance(event, QFocusEvent) and event.reason() not in KEPT_TYPING and self.typing:
            self.stop_typing()  # what was typed and never sent with Enter goes
        return super().eventFilter(watched, event)
