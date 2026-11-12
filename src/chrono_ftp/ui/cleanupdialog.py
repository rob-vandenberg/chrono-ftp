"""The Clean Up Stored Data window (FP-04): what Chrono FTP keeps, each kind with its size and a box; nothing is ticked at first, and only
what is ticked goes, after a question."""

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QMessageBox, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget

from chrono_ftp.connections.base import shown_name
from chrono_ftp.engine.cleanup import StoredKind, clean
from chrono_ftp.engine.serverinfo import size_text
from chrono_ftp.ui.plaintext import box_text, plain_label

# --- Version ------------------------------------------------------------
__version__ = "cleanupdialog.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Initial: the kinds of stored data with their size and place, none ticked; Remove asks first and removes exactly what is
#          ticked; a kind in use is greyed with its reason (FP-04, OR-20)

TITLE: str = "Clean Up Stored Data"
INTRODUCTION: str = "Chrono FTP keeps these on this computer. Tick what you want to remove; the rest stays as it is."
REMOVE_TEXT: str = "Remove…"
QUESTION_TEXT: str = "Remove these for good?\n\n{kinds}\n\nThis can't be undone."
PROBLEMS_TEXT: str = "Most of it was removed, but not all:\n\n{problems}"
NOTHING_TEXT: str = "nothing stored"
ROLE_KEY: int = Qt.ItemDataRole.UserRole
WIDTH: int = 760
HEIGHT: int = 420


class CleanupWindow(QDialog):
    def __init__(self, kinds_now: Callable[[], list[StoredKind]], removed: Callable[[set[str]], None], parent: QWidget | None = None) -> None:
        """kinds_now: what is stored at this moment; removed: told the keys of the kinds that went, so the program can follow."""
        super().__init__(parent)
        self.setWindowTitle(TITLE)
        self.kinds_now: Callable[[], list[StoredKind]] = kinds_now
        self.removed: Callable[[set[str]], None] = removed
        self.kinds: list[StoredKind] = []
        self.tree = QTreeWidget()
        self.tree.setColumnCount(3)
        self.tree.setHeaderLabels(["What", "Size", "Where"])
        self.tree.setRootIsDecorated(False)
        self.tree.itemChanged.connect(self._item_changed)
        self.remove_button = QPushButton(REMOVE_TEXT)
        self.remove_button.clicked.connect(self.remove)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)
        self.close_button.setDefault(True)  # Enter never removes anything
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.remove_button)
        buttons.addWidget(self.close_button)
        layout = QVBoxLayout(self)
        introduction = plain_label(INTRODUCTION)
        introduction.setWordWrap(True)
        layout.addWidget(introduction)
        layout.addWidget(self.tree, 1)
        layout.addLayout(buttons)
        self.resize(WIDTH, HEIGHT)
        self.fill()

    def fill(self) -> None:
        self.kinds = self.kinds_now()
        self.tree.blockSignals(True)
        self.tree.clear()
        for kind in self.kinds:
            where = ", ".join(shown_name(str(path)) for path in kind.paths)
            item = QTreeWidgetItem([kind.title, size_text(kind.size) if kind.paths else NOTHING_TEXT, where])
            item.setData(0, ROLE_KEY, kind.key)
            item.setToolTip(0, kind.busy or kind.explanation)
            item.setToolTip(2, "\n".join(shown_name(str(path)) for path in kind.paths))
            flags = item.flags() | Qt.ItemFlag.ItemIsUserCheckable
            if kind.busy or not (kind.paths or kind.passwords):
                flags &= ~Qt.ItemFlag.ItemIsEnabled  # greyed with its reason (OR-20)
            item.setFlags(flags)
            item.setCheckState(0, Qt.CheckState.Unchecked)  # nothing is ticked at first
            self.tree.addTopLevelItem(item)
        for column in (0, 1):
            self.tree.resizeColumnToContents(column)
        self.tree.blockSignals(False)
        self._update_button()

    def ticked(self) -> list[StoredKind]:
        chosen: list[StoredKind] = []
        for row, kind in enumerate(self.kinds):
            item = self.tree.topLevelItem(row)
            if item is not None and item.checkState(0) == Qt.CheckState.Checked and not kind.busy:
                chosen.append(kind)
        return chosen

    def _item_changed(self, _item: QTreeWidgetItem, _column: int) -> None:
        self._update_button()

    def _update_button(self) -> None:
        self.remove_button.setEnabled(bool(self.ticked()))

    def remove(self) -> None:
        chosen = self.ticked()
        if not chosen:
            return
        question = QUESTION_TEXT.format(kinds="\n".join(f"{kind.title} ({size_text(kind.size)})" for kind in chosen))
        if QMessageBox.question(self, TITLE, box_text(question)) != QMessageBox.StandardButton.Yes:
            return
        problems = clean(chosen)
        self.removed({kind.key for kind in chosen})
        if problems:
            QMessageBox.information(self, TITLE, box_text(PROBLEMS_TEXT.format(problems="\n".join(problems))))
        self.fill()
