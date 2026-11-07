import os
from pathlib import Path

# --- Version ------------------------------------------------------------
__version__ = "trust.py 0.0.5"

# --- Version history ----------------------------------------------------
# v0.0.5: Initial: the servers the person trusted: host keys and certificates, never accepted without asking (OR-19)

KNOWN_HOSTS_NAME: str = "known_hosts"
CERTIFICATES_NAME: str = "trusted_certificates"


def config_folder() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "chrono-ftp"


def known_hosts_path() -> Path:
    return config_folder() / KNOWN_HOSTS_NAME


def certificates_path() -> Path:
    return config_folder() / CERTIFICATES_NAME


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(line.rstrip("\n") + "\n")
    path.chmod(0o600)


def remember_host_key(line: str) -> None:
    _append(known_hosts_path(), line)


def remember_certificate(line: str) -> None:
    _append(certificates_path(), line)


def lines_for(prefix: str) -> list[str]:
    """What is remembered of this server."""
    path = certificates_path()
    if not path.exists():
        return []
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line.startswith(prefix)]


def certificate_is_trusted(line: str) -> bool:
    path = certificates_path()
    return path.exists() and line in path.read_text(encoding="utf-8").splitlines()
