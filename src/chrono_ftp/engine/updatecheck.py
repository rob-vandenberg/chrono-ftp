"""The update check (FP-06): only when the person asks, GitHub is asked for the version tags of the repository, and the newest is compared
with the version that runs. Nothing is downloaded or installed, and the request holds nothing about the person."""

import json
import re
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import cast

from chrono_ftp.core.errors import ChronoFtpError

# --- Version ------------------------------------------------------------
__version__ = "updatecheck.py 0.0.38"

# --- Version history ----------------------------------------------------
# v0.0.38: Initial: the newest version tag on GitHub against the running version, through the public API with a neutral User-Agent; a
#          failure is said calmly (FP-06, D-068)

TAGS_ADDRESS: str = "https://api.github.com/repos/{owner}/{name}/tags?per_page=100"
REPOSITORY: re.Pattern[str] = re.compile(r"https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?")
VERSION: re.Pattern[str] = re.compile(r"v?([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})")  # x.y.z (PY-1); a v before it is taken too
NEXT_PAGE: re.Pattern[str] = re.compile(r"<([^>]+)>;\s*rel=\"next\"")
HEADERS: dict[str, str] = {"User-Agent": "chrono-ftp-update-check", "Accept": "application/vnd.github+json"}  # nothing about the person
SECONDS: float = 15.0
MOST_BYTES: int = 2 * 1024 * 1024  # an answer is input: more than this is no list of tags
MOST_PAGES: int = 10
NO_ADDRESS_TEXT: str = "Chrono FTP does not know where its repository is, so it can't check for a newer version."
NOT_REACHED_TEXT: str = "GitHub could not be reached just now, so the check did not finish. You can try again later."
REFUSED_TEXT: str = "GitHub did not answer the check just now ({reason}). You can try again later."
ODD_ANSWER_TEXT: str = "GitHub's answer could not be read, so the check did not finish. You can try again later."
NO_TAGS_TEXT: str = "GitHub lists no released version of Chrono FTP yet."
Fetch = Callable[[str], tuple[bytes, str]]  # an address gives the body of the answer and its Link header


class UpdateCheckFailed(ChronoFtpError):
    """The check did not finish; the text says why, calmly (OR-23)."""


@dataclass(frozen=True)
class UpdateAnswer:
    newer: bool
    newest: str  # the newest version tag on GitHub
    page: str  # where it can be seen


def version_numbers(text: str) -> tuple[int, int, int] | None:
    found = VERSION.fullmatch(text.strip())
    return (int(found.group(1)), int(found.group(2)), int(found.group(3))) if found is not None else None


def newest_tag(tags: list[str]) -> str | None:
    """The tag of the highest version; tags that are no version are left out."""
    versions = [(numbers, tag) for tag in tags if (numbers := version_numbers(tag)) is not None]
    return max(versions)[1] if versions else None


def tag_names(body: bytes) -> list[str]:
    """The names in one page of GitHub's tags; an answer that is not that list raises (OR-15)."""
    try:
        data: object = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise UpdateCheckFailed(ODD_ANSWER_TEXT) from error
    if not isinstance(data, list):
        raise UpdateCheckFailed(ODD_ANSWER_TEXT)
    names: list[str] = []
    for item in cast(list[object], data):
        name = cast(dict[str, object], item).get("name") if isinstance(item, dict) else None
        if isinstance(name, str):
            names.append(name)
    return names


def fetch(address: str) -> tuple[bytes, str]:
    """One GET of the public API, without anything about the person."""
    request = urllib.request.Request(address, headers=HEADERS)
    try:
        with urllib.request.urlopen(request, timeout=SECONDS) as answer:
            body: bytes = answer.read(MOST_BYTES + 1)
            link: str = answer.headers.get("Link", "") or ""
    except urllib.error.HTTPError as error:
        raise UpdateCheckFailed(REFUSED_TEXT.format(reason=f"{error.code} {error.reason}")) from error
    except (urllib.error.URLError, OSError, ValueError) as error:
        raise UpdateCheckFailed(NOT_REACHED_TEXT) from error
    if len(body) > MOST_BYTES:
        raise UpdateCheckFailed(ODD_ANSWER_TEXT)
    return body, link


def check(repository: str, running: str, fetcher: Fetch = fetch) -> UpdateAnswer:
    """Whether GitHub has a version newer than running. Raises UpdateCheckFailed with a calm reason."""
    found = REPOSITORY.fullmatch(repository.strip())
    if found is None:
        raise UpdateCheckFailed(NO_ADDRESS_TEXT)
    address: str | None = TAGS_ADDRESS.format(owner=found.group(1), name=found.group(2))
    tags: list[str] = []
    pages = 0
    while address is not None and pages < MOST_PAGES:
        body, link = fetcher(address)
        tags += tag_names(body)
        pages += 1
        following = NEXT_PAGE.search(link)
        address = following.group(1) if following is not None and following.group(1).startswith("https://api.github.com/") else None
    newest = newest_tag(tags)
    if newest is None:
        raise UpdateCheckFailed(NO_TAGS_TEXT)
    now = version_numbers(running)
    latest = version_numbers(newest)
    newer = latest is not None and (now is None or latest > now)
    base = f"https://github.com/{found.group(1)}/{found.group(2)}"
    return UpdateAnswer(newer, newest, f"{base}/releases/tag/{newest}")
