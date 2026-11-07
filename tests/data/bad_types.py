# Deliberately wrong code. Used only by tests/test_type_strictness.py to prove
# that Pyrefly strict mode reports errors. Excluded from the project check.
# --- Version ------------------------------------------------------------
__version__ = "bad_types.py 0.0.1"

# --- Version history ----------------------------------------------------
# v0.0.1: Initial, from Chrono Mail

x: int = "a"


def unannotated(value):
    return value
