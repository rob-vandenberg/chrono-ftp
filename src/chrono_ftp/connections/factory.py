from chrono_ftp.connections.base import RemoteConnection
from chrono_ftp.connections.ftp import FtpConnection
from chrono_ftp.connections.ssh import SshConnection
from chrono_ftp.core.connection import FTP, Connection

# --- Version ------------------------------------------------------------
__version__ = "factory.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: the one place that knows which implementation a connection uses (D-009)


def remote_connection(connection: Connection, password: str | None) -> RemoteConnection:
    if connection.protocol == FTP:
        return FtpConnection(connection, password)
    return SshConnection(connection, password)
