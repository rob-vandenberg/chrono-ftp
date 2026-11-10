from pytestqt.qtbot import QtBot

from chrono_ftp.config import settings as saved_settings
from chrono_ftp.connections.base import Capabilities
from chrono_ftp.core.connection import FTP, SFTP
from chrono_ftp.core.transferoptions import TransferOptions
from chrono_ftp.transfers.options import NO_SPLIT_REST_TEXT, NO_SPLIT_UP_TEXT, usable
from chrono_ftp.ui.settingsdialog import SettingsDialog
from chrono_ftp.ui.transferoptionsform import SPLIT_SUMMARY, SPLIT_TEXT, TransferOptionsDialog, summary_text

# --- Version ------------------------------------------------------------
__version__ = "test_ui_split.py 0.0.13"

# --- Version history ----------------------------------------------------
# v0.0.13: Initial (FC-05): the option to split large files, greyed with its reason where the connection can't, kept as a default;
#          the summary line says it is for the file by file way only


def dialog_for(qtbot: QtBot, protocol: str, capabilities: Capabilities, upload: bool) -> TransferOptionsDialog:
    options, reasons = usable(TransferOptions(split_large=True), capabilities, protocol, upload)
    dialog = TransferOptionsDialog(options, reasons)
    qtbot.addWidget(dialog)
    return dialog


def test_the_split_option_is_greyed_with_its_reason_where_the_connection_can_not(qtbot: QtBot) -> None:
    for protocol, capabilities, upload, reason in ((FTP, Capabilities(resume=True), True, NO_SPLIT_UP_TEXT), (FTP, Capabilities(), False, NO_SPLIT_REST_TEXT)):
        greyed = dialog_for(qtbot, protocol, capabilities, upload)
        box = greyed.form.split_large
        assert (box.isEnabled(), box.isChecked(), box.toolTip()) == (False, False, reason)
    for protocol, capabilities, upload in ((FTP, Capabilities(resume=True), False), (SFTP, Capabilities(), True), (SFTP, Capabilities(), False)):
        dialog = dialog_for(qtbot, protocol, capabilities, upload)
        box = dialog.form.split_large
        assert (box.isEnabled(), box.isChecked(), box.toolTip()) == (True, True, SPLIT_TEXT)
        assert dialog.form.chosen().split_large and SPLIT_SUMMARY in summary_text(dialog.form.chosen(), upload)


def test_splitting_is_off_by_default_and_kept_as_a_default_in_settings(qtbot: QtBot) -> None:
    assert TransferOptions().split_large is False and saved_settings.load().transfer_options.split_large is False
    dialog = SettingsDialog(saved_settings.Settings())
    qtbot.addWidget(dialog)
    dialog.transfer_options.split_large.setChecked(True)
    saved_settings.save(dialog.chosen())
    assert saved_settings.load().transfer_options.split_large is True
