"""The home page's tools: listed open in the left column, under the tags, and run on a PDF."""

import dataclasses
import shutil

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QFileDialog, QLabel

from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings
from zenith_pdf.ui import pdf_tools
from zenith_pdf.ui.home import NavButton


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


def tool_rows(home):
    return [button for button in home.findChildren(NavButton) if button.key.startswith("tool:")]


def test_every_tool_is_listed_open_in_the_left_column_under_the_tags(window):
    home = window.home
    QTest.qWait(50)  # rows added to a shown column show on the next turn of the event loop
    rows = tool_rows(home)
    assert [row.key for row in rows] == ["tool:compare"] + [f"tool:{tool.key}" for tool in pdf_tools.TOOLS]
    assert all(row.isVisibleTo(home) for row in rows)  # open, no click needed
    assert not [card for card in home.cards if card.title == "PDF tools"]
    assert "tools" not in {button.key for button in home._nav_buttons}
    layout = rows[0].parentWidget().layout()
    tags = next(i for i in range(layout.count()) if (w := layout.itemAt(i).widget()) is not None
                and w.findChild(QLabel) is not None
                and w.findChild(QLabel).text() == "TAGS")
    assert layout.indexOf(rows[0]) > tags


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


def test_the_tools_in_the_left_column_run_their_tool(window):
    home = window.home
    went = []
    home.compare_requested.disconnect()  # the window's own dialogs stay closed
    home.compare_requested.connect(lambda: went.append("compare"))
    home.tool_requested.disconnect()
    home.tool_requested.connect(went.append)
    rows = {row.key: row for row in tool_rows(home)}
    rows["tool:compare"].click()
    rows["tool:compress"].click()
    assert went == ["compare", "compress"]
