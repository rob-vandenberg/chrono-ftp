"""The errors of Chrono FTP. Their text is shown to the person, so it reassures and never alarms (OR-23)."""

# --- Version ------------------------------------------------------------
__version__ = "errors.py 0.0.12"

# --- Version history ----------------------------------------------------
# v0.0.12: LocalWriteFailed and LocalReadFailed: this computer could not write what arrived, or read what is to be sent;
#          TransferInterrupted: the data of one file broke off while the login still works; NameTaken: a name was taken meanwhile;
#          ContinuationRefused and RefusedForNow: a refusal to go on with a part, and one of a file for now
# v0.0.6: LocalActionFailed
# v0.0.5: Initial


class ChronoFtpError(Exception):
    """Something that didn't work, with a text a person can read."""


class CredentialError(ChronoFtpError):
    """The credential store could not be reached."""


class ConnectionFailed(ChronoFtpError):
    """A server could not be reached, or refused the login."""


class LoginRefused(ConnectionFailed):
    """The server is there, but did not accept this login."""


class RemoteActionFailed(ChronoFtpError):
    """A server refused an action or did not finish it."""


class ConnectionLost(RemoteActionFailed):
    """The connection dropped; the tab goes grey and can be connected again."""


class LocalActionFailed(ChronoFtpError):
    """An action on this computer could not be done; the text says why."""


class LocalWriteFailed(LocalActionFailed):
    """This computer could not write what arrived (a full disk, a lost drive): no refusal of the server. What was written stays as a part,
    for the next try once the problem is gone."""


class LocalReadFailed(LocalActionFailed):
    """This computer could not read the file it was sending (a lost drive, a damaged disk): no refusal of the server and no broken line.
    What arrived stays as a part, for the next try once the problem is gone."""


class ContinuationRefused(RemoteActionFailed):
    """The server would not go on with a part (it refused to start at a position): the file starts again from the beginning."""


class RefusedForNow(RemoteActionFailed):
    """The server refused this one file for now (busy, or no room): what arrived of it is still good, and kept."""


class NameTaken(RemoteActionFailed):
    """Something has the name a new file was to take: nothing was replaced."""


class TransferInterrupted(RemoteActionFailed):
    """The data of one file broke off on the way while the login itself still works (FTP: its data connection dropped). Only that file
    is affected; what arrived stays as a part where it can be continued."""
