"""The home page's list of tools: every one listed by group, found by the search box, and run on a PDF."""

import dataclasses
import shutil

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QFileDialog

from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings
from zenith_pdf.ui import pdf_tools
from zenith_pdf.ui.home_tools import CREATE


@pytest.fixture
def window(qt_app, tmp_path):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1300, 850)
    window.show()
    QTest.qWaitForWindowExposed(window)
    window.show_home()
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


def test_every_tool_is_listed_by_group_and_the_search_finds_them(window):
    home = window.home
    nav = next(button for button in home._nav_buttons if button.key == "tools")
    assert nav.count == str(len(pdf_tools.TOOLS) + len(CREATE))
    nav.click()
    QApplication.processEvents()
    assert home.tools.isVisible() and not home.table.isVisible() and home.title.text() == "Tools"
    keys = {tile.key for tile in home.tools.tiles()}
    assert keys == {tool.key for tool in pdf_tools.TOOLS} | {key for key, *_ in CREATE}
    groups = [title.text() for title, _, _ in home.tools.groups]
    assert groups == ["Create", *pdf_tools.GROUPS]
    assert home.search.placeholderText() == "Search tools"
    home.search.setText("compress")
    assert [tile.key for tile in home.tools.tiles() if not tile.isHidden()] == ["compress"]
    assert home.summary.text() == "1 tool"
    home.search.setText("no such tool at all")
    assert home.tools.empty.isVisible()
    home.search.clear()
    home.show_scope("all")
    assert not home.tools.isVisible() and home.search.placeholderText() != "Search tools"


def test_a_document_tool_asks_for_the_pdf_then_works_on_it(window, monkeypatch, sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    ran = []
    tools = [dataclasses.replace(tool, run=lambda w, key=tool.key: ran.append((key, w.current_workspace())))
             for tool in pdf_tools.TOOLS]
    monkeypatch.setattr(pdf_tools, "TOOLS", tools)
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: (str(path), ""))
    window.home.tool_requested.emit("compress")
    QTest.qWait(100)
    (key, workspace), = ran
    assert key == "compress" and workspace.document.path == path.resolve()

    ran.clear()
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args, **kwargs: ("", ""))
    window.home.tool_requested.emit("crop")  # no file chosen: nothing happens
    window.home.tool_requested.emit("images_to_pdf")  # needs no document: runs at once
    assert [key for key, _ in ran] == ["images_to_pdf"]


def test_the_create_tools_go_where_the_home_cards_go(window):
    went = []
    home = window.home
    for name in ("new", "builder", "compare"):
        signal = getattr(home, f"{name}_requested")
        signal.disconnect()  # the window's own dialogs stay closed
        signal.connect(lambda name=name: went.append(name))
    for tile in home.tools.tiles():
        if tile.key in ("new", "builder", "compare"):
            tile.clicked.emit(tile.key)
    assert sorted(went) == ["builder", "compare", "new"]
