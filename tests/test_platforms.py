"""What differs between Windows, macOS and Linux: key names, text recognition with Tesseract, spelling with
Enchant, the camera. Each system's own part runs on that system in CI; the logic around it runs everywhere."""

from __future__ import annotations

import subprocess

from PySide6.QtGui import QImage

from zenith_pdf.core import ocr, platforms, spelling


def test_mac_writes_keys_as_symbols_and_moves_the_keys_it_keeps(monkeypatch):
    monkeypatch.setattr(platforms, "MAC", True)
    keys = platforms.native_keys
    assert keys("Ctrl+K") == "⌘K"
    assert keys("Save as (Ctrl+Shift+S)") == "Save as (⇧⌘S)"  # in the order a Mac writes them
    assert keys("Alt+Left / Alt+Right") == "⌥Left / ⌥Right"
    assert keys("Ctrl and the mouse wheel zoom") == "⌘ and the mouse wheel zoom"
    assert keys("Esc or Ctrl+H leaves") == "Esc or ⇧⌘R leaves"  # ⌘H hides the app
    assert keys("Ctrl+Home") == "⌘Home"  # not taken for the moved Ctrl+H
    assert keys("Ctrl+Tab") == "⌃Tab" and keys("F11") == "⌃⌘F"
    assert platforms.key("Ctrl+H") == "Ctrl+Shift+R" and platforms.key("Ctrl+K") == "Ctrl+K"


def test_windows_and_linux_keep_the_key_names(monkeypatch):
    monkeypatch.setattr(platforms, "MAC", False)
    assert platforms.native_keys("Ctrl+Shift+S") == "Ctrl+Shift+S"
    assert platforms.key("Ctrl+H") == "Ctrl+H"


def test_tesseract_words_come_back_in_points(monkeypatch):
    tsv = ("level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
           "1\t1\t0\t0\t0\t0\t0\t0\t1700\t2200\t-1\t\n"
           "5\t1\t1\t1\t1\t1\t200\t300\t150\t40\t96.1\tSolar\n"
           "5\t1\t1\t1\t1\t2\t370\t300\t90\t40\t95.0\t\"cells\"\n"
           "5\t1\t1\t1\t1\t3\t480\t300\t10\t40\t20.0\t \n")
    commands = []

    def fake_run(command, **options):
        commands.append(command)
        return subprocess.CompletedProcess(command, 0, tsv, "")

    monkeypatch.setattr(ocr, "_picture", lambda document, index, dpi: (QImage(8, 8, QImage.Format.Format_Grayscale8),
                                                                       2.0))
    monkeypatch.setattr(ocr, "_tesseract_languages", lambda: ["eng", "ita", "osd"])
    monkeypatch.setattr(ocr, "_system_languages", lambda: ["it-IT", "en-US"])
    monkeypatch.setattr(ocr.subprocess, "run", fake_run)
    words = ocr._tesseract(None, 0, "", 200)
    assert words == [("Solar", (100.0, 150.0, 75.0, 20.0)), ('"cells"', (185.0, 150.0, 45.0, 20.0))]
    assert commands[0][3:5] == ["-l", "ita+eng"]  # the user's language first, English after
    ocr._tesseract(None, 0, "ita", 200)
    assert commands[1][3:5] == ["-l", "ita"]
    ocr._tesseract(None, 0, "it-IT", 200)  # a Windows tag from an old setting: the system's languages instead
    assert commands[2][3:5] == ["-l", "ita+eng"]


def test_tesseract_failures_say_why(monkeypatch):
    monkeypatch.setattr(ocr, "_picture", lambda document, index, dpi: (QImage(8, 8, QImage.Format.Format_Grayscale8),
                                                                       1.0))
    monkeypatch.setattr(ocr, "_tesseract_languages", lambda: [])
    monkeypatch.setattr(ocr.subprocess, "run", lambda command, **options: subprocess.CompletedProcess(
        command, 1, "", "Error opening data file\nFailed loading language 'eng'"))
    try:
        ocr._tesseract(None, 0, "", 200)
    except RuntimeError as error:
        assert "Failed loading language" in str(error)
    else:
        raise AssertionError("no error")


class FakeDictionary:
    WORDS = {"the", "cat", "sat", "on", "mat", "anno", "acqua", "il"}

    def check(self, word):
        return word.lower() in self.WORDS

    def suggest(self, word):
        return ["The", "Tea"] if word == "Teh" else []


def test_enchant_flags_unknown_words_and_doubled_ones(monkeypatch):
    checker = spelling._Enchant()
    checker.broker = object()  # ready
    checker.dictionaries["en-US"] = FakeDictionary()
    issues = checker.issues("en-US", "Teh cat sat on the the mat. PDF l'anno dell'acqua x", 200)
    assert [(i.word, i.action, i.suggestions[:1]) for i in issues] == [("Teh", "suggest", ("The",)),
                                                                       ("the", "delete", ())]
    assert issues[1].start == 19  # the second "the": the dialog takes the space before it too


def test_doubled_words_are_found_once_and_win_over_spelling():
    flagged = [spelling.Issue(12, 3, "teh", "suggest", ("the",))]
    issues = spelling._with_doubled("Write it in teh teh box", flagged)
    assert [(i.start, i.action) for i in issues] == [(12, "suggest"), (16, "delete")]
    assert spelling._with_doubled("no doubles here", []) == []


def test_live_camera_dialog_offers_a_file_without_starting_the_camera(qt_app, monkeypatch):
    from zenith_pdf.ui import insert_dialogs

    asked = []
    monkeypatch.setattr(insert_dialogs.LiveCameraDialog, "_ask_permission", lambda self: asked.append(self))
    dialog = insert_dialogs.LiveCameraDialog()
    assert dialog.photo == "" and dialog.temporary
    assert not dialog._take.isEnabled()  # until the camera is ready
    dialog.reject()
    dialog.deleteLater()
