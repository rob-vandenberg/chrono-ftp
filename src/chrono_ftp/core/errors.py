"""The errors of Chrono FTP. Their text is shown to the person, so it reassures and never alarms (OR-23)."""

# --- Version ------------------------------------------------------------
__version__ = "errors.py 0.0.6"

# --- Version history ----------------------------------------------------
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
