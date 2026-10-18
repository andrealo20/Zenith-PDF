"""Snapping, sessions, the home page, the guide and thumbnails with annotations."""

import os
import shutil
import time
from pathlib import Path

import pytest
from PySide6.QtCore import QPointF, QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.core.annotations import Shape
from zenith_pdf.settings import Settings
from zenith_pdf.theme import themes
from zenith_pdf.ui import home as home_module
from zenith_pdf.ui import messages
from zenith_pdf.ui.guide import GuideWindow
from zenith_pdf.ui.home import AUTHOR, FILE_ROLE, LOCATION, NAME, OPENED, TAG_COLOURS, TAGS, HomePage, ago


def wait(ms=40):
    QTest.qWait(ms)
    QApplication.processEvents()


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1400, 900)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


@pytest.fixture
def pdf(sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    return path


def test_snapping_lines_annotations_up(window, pdf):
    window.open_file(pdf)
    workspace = window.current_workspace()
    view = workspace.view
    anchor = Shape(page=0, rect=(100, 100, 80, 60))
    view.add_annotations([anchor], "Shape")
    snapper = view.snapper
    assert snapper.snap_rect(0, (103, 300, 40, 40), set()) == (0.0, 0.0)  # off by default
    window.settings.set("snap_annotations", True)
    dx, dy = snapper.snap_rect(0, (103, 300, 40, 40), set())
    assert dx == pytest.approx(-3.0) and dy == 0.0  # left edges meet
    assert snapper._guides  # and a guide shows it
    point = snapper.snap_point(0, QPointF(178, 158))
    assert (point.x(), point.y()) == pytest.approx((180, 160))  # the corner
    snapper.hide()
    window.settings.set("snap_annotations", False)
    window.settings.set("snap_content", True)
    assert snapper.snap_point(0, QPointF(2, 400)).x() == pytest.approx(0.0)  # the page's edge
    window.settings.set("snap_content", False)
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_documents_reopen_where_they_were(window, pdf):
    window.settings.set("remember_position", True)
    window.open_file(pdf)
    workspace = window.current_workspace()
    workspace.view.set_zoom(2.0)
    workspace.view.goto_page(2)
    window.close_tab(window.workspaces.index(workspace), confirm=False)
    window.open_file(pdf)
    wait(80)
    view = window.current_workspace().view
    assert view.current_page == 2 and view.zoom == pytest.approx(2.0)

    window.close()  # writes the session
    session = window.settings.get("session")
    assert [t["path"] for t in session["tabs"]] == [str(pdf.resolve())]
    window.show()
    window.restore_session()
    wait(80)
    assert window.current_workspace().document.path == pdf.resolve()
    assert window.current_workspace().view.current_page == 2
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)


def test_home_lists_stars_and_tags(window, pdf, tmp_path):
    other = tmp_path / "other.pdf"
    shutil.copy(pdf, other)
    window.settings.add_recent(pdf)
    window.settings.add_recent(other)
    window.show_home()
    home = window.home
    home.toggle_star(pdf)
    tags = window.settings.get("file_tags")
    tags[str(other)] = ["Exam"]
    window.settings.set("file_tags", tags)
    home.refresh()
    names = [home.proxy.data(home.proxy.index(r, 1), FILE_ROLE).path.name for r in range(home.proxy.rowCount())]
    assert set(names) >= {"doc.pdf", "other.pdf"}
    home.show_scope("starred")
    assert [e.path.name for e in home.model.entries] == ["doc.pdf"]
    home.show_scope("tag:Exam")
    assert [e.path.name for e in home.model.entries] == ["other.pdf"]
    home.show_scope("all")
    home.search.setText("other")
    assert home.proxy.rowCount() == 1
    home.search.clear()
    for _ in range(100):  # page counts and thumbnails come from a background thread
        wait(20)
        if all(e.pages for e in home.model.entries if e.path in (pdf, other)):
            break
    assert all(e.pages == 4 for e in home.model.entries if e.path in (pdf, other))
    assert ago(None) == "" and ago(__import__("time").time()) == "Just now"


def test_guide_has_every_chapter_and_searches(window):
    guide = GuideWindow(window)
    keys = [guide.list.item(r).data(256) for r in range(guide.list.count())]
    assert {"start", "tools", "pdf-tools", "shortcuts", "whats-new"} <= set(keys)
    guide.show_chapter("tools")
    assert "Laser pointer" in guide.text.toPlainText()  # written from the tool catalogue
    guide.search.setText("redact")
    visible = [guide.list.item(r).text() for r in range(guide.list.count()) if not guide.list.item(r).isHidden()]
    assert "Redaction and security" in visible and "Doc Builder" not in visible
    guide.close()


def test_thumbnails_show_annotations(window, pdf):
    window.open_file(pdf)
    workspace = window.current_workspace()
    model = workspace.thumbnails.model
    model.data(model.index(0), 1)  # asks for the thumbnail
    for _ in range(200):
        wait(20)
        if 0 in model._bases:
            break
    before = model._pixmaps[0].toImage()
    workspace.view.add_annotations([Shape(page=0, rect=(0, 0, 300, 300), fill=(1, 0, 0), color=(1, 0, 0))], "Shape")
    wait(250)
    after = model._pixmaps[0].toImage()
    assert before.pixelColor(10, 10) != after.pixelColor(10, 10)
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_home_fits_a_narrow_window(window, pdf):
    window.settings.add_recent(pdf)
    home = HomePage(window.settings)
    try:
        home.resize(960, 700)
        home.show()
        wait()
        assert home.minimumSizeHint().width() <= 960
        assert not home.aside.isVisible()
        assert home.table.columnWidth(NAME) >= 260
        assert home.table.isColumnHidden(AUTHOR) and not home.table.isColumnHidden(OPENED)
        home.resize(1400, 900)
        wait()
        assert home.aside.isVisible() and {card.mode for card in home.cards} == {"full"}
        assert not any(home.table.isColumnHidden(c) for c in (LOCATION, AUTHOR, OPENED, TAGS))
        assert home.table.columnWidth(NAME) >= 260
        for button in (home.search, home.open_button, home.new_button):
            assert button.height() == 36
    finally:
        home.close()
        home.deleteLater()


def test_new_tags_get_colours_of_their_own(window, pdf, monkeypatch, tmp_path):
    others = []
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        path = tmp_path / name
        shutil.copy(pdf, path)
        others.append(path)
    home = window.home
    answers = iter(["Datasheet", "Electronics", "Tests"])  # three names that used to hash to the same red
    monkeypatch.setattr(messages, "get_item", lambda *a, **k: next(answers))
    for path in others:
        home.add_tag(path)
    colours = window.settings.get("tag_colors")
    picked = [colours[name] for name in ("Datasheet", "Electronics", "Tests")]
    assert len(set(picked)) == 3 and "#ef4444" not in picked and all(c in TAG_COLOURS for c in picked)
    home.refresh()
    assert [home.tag_colour(n) for n in ("Datasheet", "Electronics", "Tests")] == picked  # they stay


def test_quick_cards_and_the_search_icon_follow_the_theme(window):
    card = window.home.cards[0]
    before = card.glyph.pixmap().toImage()
    search = window.home._search_icon
    glass = search.icon().pixmap(16, 16).toImage()
    theme, accent = window.settings.theme, window.settings.get("accent")
    themes.choose(theme, "green" if accent != "green" else "blue")
    try:
        assert card.glyph.pixmap().toImage() != before
        themes.choose("light" if themes.current.name == "dark" else "dark", accent)
        assert search.icon().pixmap(16, 16).toImage() != glass  # else it vanishes on the other background
    finally:
        themes.choose(theme, accent)
    assert card.glyph.pixmap().toImage() == before
    assert search.icon().pixmap(16, 16).toImage() == glass


def test_thumbnails_stay_in_the_test_cache_and_are_pruned(window, pdf, monkeypatch):
    loader = window.home.loader
    assert Path(os.environ["ZENITH_CACHE_DIR"]) in loader.folder.parents
    stale = loader.thumbnail_file(Path("C:/gone.pdf"), 1.0)
    stale.write_bytes(b"png")
    infos = {"C:/gone.pdf": {"mtime": 1.0, "size": 1, "pages": 1, "author": "", "title": ""},
             **window.settings.get("file_info")}  # the oldest entry comes first
    window.settings.set("file_info", infos)
    count = len(infos) + (0 if str(pdf) in infos else 1)  # after the new facts are in
    monkeypatch.setattr(home_module, "INFO_LIMIT", count - 1)
    window.home._on_loaded(str(pdf), {"mtime": time.time(), "size": 1, "pages": 4}, home_module.QImage())
    assert not stale.exists()  # trimmed from the list, so its thumbnail went too
    assert "C:/gone.pdf" not in window.settings.get("file_info") and str(pdf) in window.settings.get("file_info")


def test_file_info_threads_stop_when_asked(window, pdf, tmp_path):
    copies = []
    for number in range(6):
        path = tmp_path / f"copy{number}.pdf"
        shutil.copy(pdf, path)
        copies.append(path)
    loader = home_module.InfoLoader()
    loader.request(copies)
    loader.shutdown()
    assert not any(thread.is_alive() for thread in loader._threads)
    loader.request(copies)  # after shutdown nothing new starts
    assert not any(thread.is_alive() for thread in loader._threads)
    loader.deleteLater()


def test_a_narrow_home_opens_its_left_column_from_the_menu(window):
    size = window.size()
    home = window.home
    window.show_home()
    try:
        window.resize(400, 800)
        wait()
        assert window.width() <= 420 and home.width() <= 420
        assert home.menu_button.isVisible() and not home.nav.isVisible()
        home.menu_button.click()
        assert home.nav.isVisible() and home.scrim.isVisible() and home.nav.x() == 0
        home.show_scope("starred")  # picking something closes it
        assert not home.nav.isVisible() and not home.scrim.isVisible()
        home.toggle_nav()
        home.scrim.click()  # so does a tap on the files
        assert not home.nav.isVisible()
        home.show_scope("all")
    finally:
        window.resize(size)
        wait()
    assert home.nav.isVisible() and not home.menu_button.isVisible()
