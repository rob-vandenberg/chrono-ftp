import json
from pathlib import Path

from chrono_ftp.config import workspaces as saved_workspaces
from chrono_ftp.config.connections import config_folder
from chrono_ftp.config.workspaces import FILE_NAME, Workspace, WorkspaceTab, Workspaces

# --- Version ------------------------------------------------------------
__version__ = "test_config_workspaces.py 0.0.45"

# --- Version history ----------------------------------------------------
# v0.0.45: Initial: the workspaces file: saved and read back, a damaged file and a damaged entry, and the one that opens at start (FI-02)


def file_path() -> Path:
    return config_folder() / FILE_NAME


def write_raw(text: str) -> None:
    file_path().parent.mkdir(parents=True, exist_ok=True)
    file_path().write_text(text, encoding="utf-8")


HOME: Workspace = Workspace("Home", (WorkspaceTab("Shop", "/home/me", "/var/www"), WorkspaceTab("Other", "/tmp", "/")))


def test_a_workspace_comes_back_exactly_as_it_was_saved() -> None:
    saved_workspaces.save(Workspaces((HOME,), "Home"))
    kept = saved_workspaces.load()
    assert kept.items == (HOME,)
    assert kept.at_start == "Home"


def test_nothing_is_kept_before_anything_is_saved() -> None:
    assert saved_workspaces.load() == Workspaces()


def test_a_damaged_file_gives_no_workspaces() -> None:
    write_raw("{ this is not json")
    assert saved_workspaces.load() == Workspaces()


def test_a_damaged_workspace_is_left_out_and_the_others_stay() -> None:
    write_raw(json.dumps({"items": [{"name": ""}, {"name": "Good", "tabs": [{"connection": "Shop"}, {"connection": 7}]}]}))
    kept = saved_workspaces.load()
    assert [item.name for item in kept.items] == ["Good"]
    assert kept.items[0].tabs == (WorkspaceTab("Shop"),)


def test_a_workspace_that_opens_at_start_but_is_gone_opens_nothing() -> None:
    write_raw(json.dumps({"items": [{"name": "Good", "tabs": []}], "at_start": "Away"}))
    assert saved_workspaces.load().at_start == ""


def test_a_second_workspace_of_the_same_name_is_left_out() -> None:
    write_raw(json.dumps({"items": [{"name": "Home", "tabs": []}, {"name": "home", "tabs": [{"connection": "Shop"}]}]}))
    assert len(saved_workspaces.load().items) == 1


def test_saving_under_a_name_that_is_there_replaces_that_one_in_its_place() -> None:
    kept = Workspaces((Workspace("One"), Workspace("Two")))
    changed = saved_workspaces.with_workspace(kept, Workspace("One", (WorkspaceTab("Shop"),)))
    assert [item.name for item in changed.items] == ["One", "Two"]
    assert changed.items[0].tabs == (WorkspaceTab("Shop"),)


def test_deleting_the_workspace_that_opens_at_start_leaves_none_opening_at_start() -> None:
    kept = Workspaces((Workspace("One"), Workspace("Two")), "One")
    changed = saved_workspaces.without(kept, "One")
    assert [item.name for item in changed.items] == ["Two"]
    assert changed.at_start == ""


def test_renaming_the_workspace_that_opens_at_start_keeps_it_opening_at_start() -> None:
    changed = saved_workspaces.renamed(Workspaces((Workspace("One"),), "One"), "One", "Two")
    assert changed.at_start == "Two"
    assert [item.name for item in changed.items] == ["Two"]


def test_a_name_that_is_no_workspace_opens_nothing_at_start() -> None:
    assert saved_workspaces.with_start(Workspaces((Workspace("One"),)), "Away").at_start == ""
