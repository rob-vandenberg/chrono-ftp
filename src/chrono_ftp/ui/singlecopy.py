"""One Chrono FTP at a time (D-051): a second start brings the running copy to the front and quits."""

import getpass
from collections.abc import Callable

from PySide6.QtCore import QObject
from PySide6.QtNetwork import QLocalServer, QLocalSocket

# --- Version ------------------------------------------------------------
__version__ = "singlecopy.py 0.0.25"

# --- Version history ----------------------------------------------------
# v0.0.25: Initial: a local socket per user; a socket left behind by a crash is removed

WAIT_MILLISECONDS: int = 1000
SHOW_MESSAGE: bytes = b"show"


def socket_name() -> str:
    """One name per user, so two people on one computer each have their own Chrono FTP."""
    return f"chrono-ftp-{getpass.getuser()}"


def tell_running_copy(name: str) -> bool:
    """True when a copy is running: it has been told to come to the front, and this start ends."""
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(WAIT_MILLISECONDS):
        return False  # nobody listens, also when a crash left the socket behind
    socket.write(SHOW_MESSAGE)
    socket.waitForBytesWritten(WAIT_MILLISECONDS)
    socket.disconnectFromServer()
    return True


class SingleCopy(QObject):
    """Listens for later starts and calls come_forward for each."""

    def __init__(self, name: str, come_forward: Callable[[], None], parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.come_forward: Callable[[], None] = come_forward
        self.server: QLocalServer = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        if not self.server.listen(name):
            QLocalServer.removeServer(name)  # left behind by a copy that crashed: nobody answered on it
            self.server.listen(name)
        self.server.newConnection.connect(self._new_start)

    def listening(self) -> bool:
        return self.server.isListening()

    def _new_start(self) -> None:
        while self.server.hasPendingConnections():
            socket = self.server.nextPendingConnection()
            if socket is not None:
                socket.disconnected.connect(socket.deleteLater)
        self.come_forward()

    def close(self) -> None:
        self.server.close()
