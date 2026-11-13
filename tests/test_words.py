"""Every sentence the person reads that holds a number: how it reads for one, for none and for many (OR-23)."""

from chrono_ftp.ui.plaintext import counted, was
from chrono_ftp.ui.startabort import aborted_text, finished_text

# --- Version ------------------------------------------------------------
__version__ = "test_words.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Initial: the sentences of a run that is through or aborted, and the number-and-word helper, read as a person writes them


def test_a_run_of_one_thing_never_says_all_one_were_done() -> None:
    assert finished_text(1, 1, 0) == "Finished: the one thing was done."
    assert finished_text(0, 1, 1) == "Finished: the one thing could not be done."


def test_one_of_many_was_done_never_were() -> None:
    assert finished_text(1, 2, 1) == "Finished: 1 of 2 was done; 1 could not be."
    assert finished_text(3, 5, 2) == "Finished: 3 of 5 were done; 2 could not be."
    assert finished_text(12, 12, 0) == "Finished: all 12 were done."


def test_an_abort_says_was_and_were_where_they_belong() -> None:
    assert aborted_text(1, 6, 5) == "Aborted: 1 of 6 was done, and 5 were not. What was done stays done."
    assert aborted_text(2, 3, 1) == "Aborted: 2 of 3 were done, and 1 was not. What was done stays done."


def test_an_abort_before_anything_was_done_says_that_plainly() -> None:
    assert aborted_text(0, 6, 6) == "Aborted: nothing was done; what was to come was not touched."


def test_a_number_comes_with_the_word_that_goes_with_it() -> None:
    assert counted(1, "file", "files") == "1 file"
    assert counted(0, "file", "files") == "0 files"
    assert counted(3, "file", "files") == "3 files"
    assert (was(1), was(0), was(2)) == ("was", "were", "were")
