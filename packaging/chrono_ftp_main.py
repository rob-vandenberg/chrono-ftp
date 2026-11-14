import sys

# --- Version ------------------------------------------------------------
__version__ = "chrono_ftp_main.py 0.0.48"

# --- Version history ----------------------------------------------------
# v0.0.48: Initial: the start of the packaged program. Nuitka compiles this file into the chrono-ftp executable, so the
#          program folder needs no Python of the system and no pyproject.toml beside it.

if __name__ == "__main__":
    from chrono_ftp.ui import main

    sys.exit(main())
