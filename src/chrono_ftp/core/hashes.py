"""The checksum algorithms Chrono FTP knows, shared by the connections, the settings and the program logic (FE-01)."""

# --- Version ------------------------------------------------------------
__version__ = "hashes.py 0.0.42"

# --- Version history ----------------------------------------------------
# v0.0.42: Initial: the algorithms, in the order the lists show them, and the length of each checksum in hex digits (FE-01)

CHECKSUM_ALGORITHMS: tuple[str, ...] = ("md5", "sha1", "sha224", "sha256", "sha384", "sha512")  # the order the lists show them in
CHECKSUM_HEX_LENGTHS: dict[str, int] = {"md5": 32, "sha1": 40, "sha224": 56, "sha256": 64, "sha384": 96, "sha512": 128}
STANDARD_ALGORITHM: str = "sha256"
