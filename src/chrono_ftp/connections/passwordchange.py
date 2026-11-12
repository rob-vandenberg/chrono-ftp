"""The talk with the server's passwd (FM-10): which question asks for the old password and which for the new one, and what of the
server's answer is shown. No password is ever kept in a text that is shown, logged or returned."""

import re
from dataclasses import dataclass

# --- Version ------------------------------------------------------------
__version__ = "passwordchange.py 0.0.37"

# --- Version history ----------------------------------------------------
# v0.0.37: Initial: the questions of passwd answered, each at most as often as passwd asks it once, and its answer without the
#          questions and without any password (D-062); a password is taken out where the server echoes it, and inside its messages
#          only where it can't be a word of them (it holds more than letters), so a common word never garbles the server's answer

PASSWD_COMMAND: str = "env LC_ALL=C passwd"  # the questions in English on every system; env works in every login shell
QUESTION: re.Pattern[str] = re.compile(r"password[^\n]*:\s*$", re.IGNORECASE)
OLD_WORDS: tuple[str, ...] = ("current", "old")
AGAIN_WORDS: tuple[str, ...] = ("retype", "again", "re-enter", "reenter", "repeat", "confirm", "verify")
NEW_WORDS: tuple[str, ...] = ("new",)
MOST_SHOWN_CHARACTERS: int = 600  # what the window shows of the server's answer
HIDDEN: str = "…"  # where a long answer is cut
ECHOED: re.Pattern[str] = re.compile(r"(.*password[^:\n]*:)\s*(.+)$", re.IGNORECASE)  # a question with the typed answer after it
CONTROL: re.Pattern[str] = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]|\x1b\[[0-9;?]*[A-Za-z]")


@dataclass(frozen=True)
class PasswordAnswer:
    changed: bool
    text: str  # what the server said, without its questions and without any password


class AskedTooOften(Exception):
    """passwd asked once more than it does when all goes well (a new password it refused, asked again): the talk ends there, and its
    answer says why."""


class PasswordTalk:
    """Answers the questions of passwd: the old password once (passwd as root asks none), the new one twice."""

    def __init__(self, old: str, new: str) -> None:
        self._old: str = old
        self._new: str = new
        self.old_given: int = 0
        self.new_given: int = 0

    def reply(self, screen: str) -> str | None:
        """What to type for the screen since the last answer; None while it asks nothing yet."""
        last = screen.rstrip(" \t").splitlines()[-1] if screen.strip() else ""
        if not QUESTION.search(last):
            return None
        question = last.lower()
        if any(word in question for word in AGAIN_WORDS) or (any(word in question for word in NEW_WORDS) and not any(word in question for word in OLD_WORDS)):
            if self.new_given >= 2:
                raise AskedTooOften()
            self.new_given += 1
            return self._new
        if self.old_given >= 1 or self.new_given:
            raise AskedTooOften()  # a plain "Password:" after the new one was given: something else asks
        self.old_given += 1
        return self._old


def _erased(text: str) -> str:
    """The text as the terminal shows it: a backspace takes the character before it away."""
    shown: list[str] = []
    for character in text:
        if character == "\b":
            if shown and shown[-1] != "\n":
                shown.pop()
        else:
            shown.append(character)
    return "".join(shown)


def shown_answer(transcript: str, secrets: tuple[str, ...]) -> str:
    """The server's own words for the window: no question of passwd, never a password even where a terminal echoed it, short."""
    lines: list[str] = []
    text = CONTROL.sub("", _erased(transcript.replace("\r\n", "\n").replace("\r", "\n")))  # what a terminal moves with is no text
    typed = {secret for secret in secrets if secret}
    for line in text.splitlines():
        clean = line.strip()
        echoed = ECHOED.match(clean)
        if not clean or clean in typed or (echoed is not None and echoed.group(2).strip() in typed):
            continue  # an echo of what was typed: a line that is a password, or a question with the answer after it
        if QUESTION.search(clean):
            continue
        for secret in typed:
            if any(not character.isalpha() for character in secret):  # never a word of the server's own message
                clean = re.sub(rf"(?<![A-Za-z0-9]){re.escape(secret)}(?![A-Za-z0-9])", "", clean)
        lines.append(clean.strip())
    text = "\n".join(lines)
    return text if len(text) <= MOST_SHOWN_CHARACTERS else text[:MOST_SHOWN_CHARACTERS] + HIDDEN
