"""The home page, the Doc Builder, the markup library and the new-file panel: what each says when it has nothing to
show, what lights under the pointer, and how a picture or a panel comes in. The suite runs with motion off; a test
of something moving asks for it with the motion_on fixture."""

import shutil

import pytest
from PySide6.QtCore import QEvent, QMimeData, QPoint, QPointF, QRect, QSettings, Qt, QUrl
from PySide6.QtGui import QColor, QDragEnterEvent, QDragLeaveEvent, QEnterEvent, QImage, QPainter
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QStyle, QStyleOptionViewItem

from conftest import settle, wait_for
from zenith_pdf.core.annotations import Shape
from zenith_pdf.core.render import RenderPool
from zenith_pdf.settings import Settings
from zenith_pdf.theme import qcolor, themes
from zenith_pdf.ui import doc_builder
from zenith_pdf.ui.doc_builder import DocBuilder
from zenith_pdf.ui.home import NAME, ROW_HEIGHT, STAR, TAGS, FileEntry, HomePage
from zenith_pdf.ui.library import Library, LibraryPanel
from zenith_pdf.ui.new_file import NewFilePanel, Row


def make_settings(tmp_path) -> Settings:
    return Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))


@pytest.fixture
def home(qt_app, tmp_path):
    page = HomePage(make_settings(tmp_path))
    page.resize(1100, 700)
    page.show()
    yield page
    page.loader.shutdown()
    page.close()
    page.deleteLater()


class Recorder(QPainter):
    """A painter that keeps the words it is asked to draw."""

    def __init__(self, device):
        super().__init__(device)
        self.words = []

    def drawText(self, *args):
        self.words.append(args[-1])
        super().drawText(*args)


def paint_cell(home, entry: FileEntry, column: int, width: int = 150, over: bool = False):
    """The words and the picture of one cell of the file list, drawn by its delegate."""
    if entry not in home.model.entries:
        home.model.set_entries([entry])
    image = QImage(width + 20, ROW_HEIGHT, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, width, ROW_HEIGHT)
    option.font = home.table.font()
    if over:
        option.state |= QStyle.StateFlag.State_MouseOver
    painter = Recorder(image)
    index = home.proxy.mapFromSource(home.model.index(home.model.entries.index(entry), column))
    home.table.itemDelegate().paint(painter, option, index)
    painter.end()
    return painter.words, image


# The home page ------------------------------------------------------------------------------------------


def test_an_empty_list_says_what_is_missing_from_the_list_it_is(home):
    assert home.empty.title.text() == "No files yet" and home.empty.isVisibleTo(home)
    for scope, glyph in (("starred", "star"), ("tag:Exam", "tag"), ("folder:/nowhere", "folder-simple")):
        home.show_scope(scope)
        assert home.empty.title.text() == "Nothing here yet." and home.empty.glyph.icon_name == glyph, scope
        assert home.empty.isVisibleTo(home) and not home.table.isVisibleTo(home)


def test_a_search_that_finds_no_file_says_so_where_the_files_would_be(home, tmp_path, sample_pdf):
    path = tmp_path / "a.pdf"
    shutil.copy(sample_pdf, path)
    home._settings.add_recent(path)
    home.refresh()
    assert not home.nothing.isVisibleTo(home)
    home.search.setText("zzzz")
    assert home.proxy.rowCount() == 0 and home.nothing.isVisibleTo(home) and home.nothing.glyph.isVisibleTo(home)
    home.inside.indexer = type("Index", (), {"search": lambda self, text: []})()
    home.search.setText("zzzzz")  # results from inside the documents are listed too: a line is enough then
    assert home.inside.isVisibleTo(home) and not home.nothing.glyph.isVisibleTo(home)
    home.search.setText("a")
    assert not home.nothing.isVisibleTo(home)


def test_tags_that_do_not_fit_are_counted_and_one_that_is_too_long_is_cut(home, tmp_path):
    many = FileEntry(path=tmp_path / "a.pdf", tags=["Energy", "Exam", "Machines", "Thermodynamics", "Optics"])
    words, image = paint_cell(home, many, TAGS)
    chips = [word for word in words if not word.startswith("+")]
    assert chips and words[len(chips):] == [f"+{len(many.tags) - len(chips)}"]
    assert {image.pixel(x, y) for x in range(152, 170) for y in range(ROW_HEIGHT - 4)} == {0}  # none past the cell
    few = FileEntry(path=tmp_path / "b.pdf", tags=["A", "B"])
    assert paint_cell(home, few, TAGS)[0] == ["A", "B"]
    long = FileEntry(path=tmp_path / "c.pdf", tags=["A tag much too long for the column it is in"])
    words, _ = paint_cell(home, long, TAGS, width=120)
    assert len(words) == 1 and words[0].endswith("…") and words[0] != long.tags[0]


def test_the_row_under_the_pointer_is_lit_whole_and_the_pointer_shows_a_hand(home, tmp_path):
    entries = [FileEntry(path=tmp_path / "a.pdf"), FileEntry(path=tmp_path / "b.pdf")]
    home.model.set_entries(entries)
    first, second = QPoint(100, ROW_HEIGHT // 2), QPoint(100, ROW_HEIGHT + ROW_HEIGHT // 2)
    assert home.row_light(entries[0].path) == 0.0
    _, dark = paint_cell(home, entries[0], NAME)
    assert dark.pixelColor(1, 1).alpha() == 0
    home._point(first)  # motion is off: it lands at once
    assert home.row_light(entries[0].path) == 1.0
    assert home.table.viewport().cursor().shape() == Qt.CursorShape.PointingHandCursor
    _, lit = paint_cell(home, entries[0], NAME)  # the cell is not the one under the pointer, the row is
    assert lit.pixelColor(1, 1).alpha() > 0 and lit.pixelColor(1, ROW_HEIGHT - 1).alpha() > 0
    _, glow = paint_cell(home, entries[0], STAR, width=36)
    assert glow.pixelColor(1, 1) == lit.pixelColor(1, 1)  # the same fill in every cell
    home._point(second)
    assert home.row_light(entries[0].path) == 0.0 and home.row_light(entries[1].path) == 1.0
    home._point(QPoint(100, 4 * ROW_HEIGHT))  # below the rows
    assert home.row_light(entries[1].path) == 0.0
    assert home.table.viewport().cursor().shape() == Qt.CursorShape.ArrowCursor


def test_a_light_that_changes_paints_again_only_its_own_row(home, tmp_path, sample_pdf):
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        shutil.copy(sample_pdf, tmp_path / name)
        home._settings.add_recent(tmp_path / name)
    home.refresh()
    painted = []
    home.table.viewport().update = lambda *where: painted.append(where)  # what the list is asked to paint again
    home._point(QPoint(100, ROW_HEIGHT + 10))  # the second row
    assert painted == [(0, ROW_HEIGHT, home.table.viewport().width(), ROW_HEIGHT)]


def test_a_row_under_a_pressed_button_takes_the_pressed_fill(home, tmp_path):
    entry = FileEntry(path=tmp_path / "a.pdf")
    home.model.set_entries([entry])
    home.table.show()  # the pointer reaches only what is shown
    at = QPoint(100, ROW_HEIGHT // 2)
    QTest.mouseMove(home.table.viewport(), at)
    _, hovered = paint_cell(home, entry, NAME)
    QTest.mousePress(home.table.viewport(), Qt.MouseButton.LeftButton, pos=at)
    assert home.row_pressed(entry.path)
    _, pressed = paint_cell(home, entry, NAME)
    QTest.mouseRelease(home.table.viewport(), Qt.MouseButton.LeftButton, pos=at)
    assert not home.row_pressed(entry.path)
    assert pressed.pixelColor(1, 1).alpha() > hovered.pixelColor(1, 1).alpha() > 0
    QTest.mousePress(home.table.viewport(), Qt.MouseButton.RightButton, pos=at)
    assert not home.row_pressed(entry.path)  # only the left button clicks a row
    QTest.mouseRelease(home.table.viewport(), Qt.MouseButton.RightButton, pos=at)


def test_the_hand_goes_when_the_row_under_the_pointer_does(home, tmp_path, sample_pdf):
    for name in ("a.pdf", "b.pdf"):
        shutil.copy(sample_pdf, tmp_path / name)
        home._settings.add_recent(tmp_path / name)
    home.refresh()
    viewport = home.table.viewport()
    last = QPoint(100, home.table.rowViewportPosition(1) + ROW_HEIGHT // 2)
    QTest.mouseMove(viewport, last)
    assert viewport.cursor().shape() == Qt.CursorShape.PointingHandCursor
    home._settings.remove_recent(home.model.entries[home.proxy.mapToSource(home.proxy.index(1, 0)).row()].path)
    assert home.proxy.rowCount() == 1 and home._pointed is None  # the pointer is on empty ground now
    assert viewport.cursor().shape() == Qt.CursorShape.ArrowCursor


def test_a_row_lights_in_quickly_and_lingers_as_the_pointer_goes(home, tmp_path, motion_on):
    entries = [FileEntry(path=tmp_path / "a.pdf"), FileEntry(path=tmp_path / "b.pdf")]
    home.model.set_entries(entries)
    home.table.show()  # an animation runs only on what is shown
    home._point(QPoint(100, ROW_HEIGHT // 2))
    assert home.row_light(entries[0].path) < 1.0
    assert wait_for(lambda: home.row_light(entries[0].path) == 1.0)
    home._point(QPoint(100, ROW_HEIGHT + ROW_HEIGHT // 2))
    assert home.row_light(entries[0].path) > 0.0  # on its way down, not gone
    assert wait_for(lambda: home.row_light(entries[0].path) == 0.0 and home.row_light(entries[1].path) == 1.0)


def test_a_page_picture_that_arrives_fades_in_over_its_placeholder(home, tmp_path, motion_on):
    path = tmp_path / "a.pdf"
    entry = FileEntry(path=path)
    home.model.set_entries([entry])
    home.table.show()
    home._entries_by_path = {str(path): entry}
    picture = QImage(60, 80, QImage.Format.Format_RGB32)
    picture.fill(QColor("white"))
    assert home.thumb_shown(path) == 1.0  # nothing on its way: placeholder or finished picture
    home._on_loaded(str(path), {"mtime": 1.0, "size": 1, "pages": 1}, picture)
    assert entry.thumbnail is not None and home.thumb_shown(path) < 1.0
    assert wait_for(lambda: home.thumb_shown(path) == 1.0)


def test_a_star_grows_into_its_new_look(home, tmp_path, sample_pdf, motion_on):
    path = tmp_path / "a.pdf"
    shutil.copy(sample_pdf, path)
    home._settings.add_recent(path)
    home.refresh()
    assert home.star_size(path) == 1.0
    home.toggle_star(path)
    assert home.model.entries[0].starred and home.star_size(path) < 1.0
    assert wait_for(lambda: home.star_size(path) == 1.0)


def test_a_new_list_arrives_and_the_left_column_of_a_narrow_home_slides_in(home, motion_on):
    home.show_scope("starred")
    assert home.empty.graphicsEffect() is not None  # the list that took the place of the other fades in and rises
    assert wait_for(lambda: home.empty.graphicsEffect() is None)  # and leaves nothing on it
    home.show_scope("starred")
    assert home.empty.graphicsEffect() is None  # the same list again is no news
    home.resize(500, 600)
    settle(50)
    assert home.menu_button.isVisible() and not home.nav.isVisible()
    home.toggle_nav()
    assert home.nav.isVisible() and home.nav.graphicsEffect() is not None and home.scrim.graphicsEffect() is not None
    assert wait_for(lambda: home.nav.graphicsEffect() is None and home.scrim.graphicsEffect() is None)
    assert home.nav.isVisible() and home.nav.x() == 0  # where it stands is where it was put, whatever was painted


def test_a_quick_card_is_reached_and_pressed_from_the_keyboard(home):
    card = home.cards[0]
    assert card.focusPolicy() == Qt.FocusPolicy.StrongFocus
    pressed = []
    card.clicked.connect(lambda: pressed.append(True))
    for key in (Qt.Key.Key_Return, Qt.Key.Key_Space):
        QTest.keyClick(card, key)
    QTest.keyClick(card, Qt.Key.Key_A)
    assert pressed == [True, True]


def test_a_nav_row_paints_the_fill_under_the_pointer_and_leaves_the_chosen_one_to_the_sheet(home):
    chosen, other = home._nav_buttons[0], home._nav_buttons[1]
    assert chosen.isChecked() and not other.isChecked()
    other._settle(True)
    assert other._fill.colour == qcolor(themes.current.hover)
    other._settle(False)
    assert other._fill.colour.alpha() == 0
    chosen._settle(True)
    assert chosen._fill.colour.alpha() == 0  # its fill is the style's own mark for what is chosen
    assert "transparent" in home.nav.styleSheet()  # the sheet's hover is off, or the fill would be drawn twice


def test_choosing_a_list_keeps_the_rows_of_the_left_column_where_the_keyboard_is(home):
    rows = list(home._nav_buttons)
    rows[1].click()  # Starred
    assert home.scope == "starred" and home._nav_buttons == rows  # made again, the row the keyboard is on would be lost
    assert [row.isChecked() for row in rows[:2]] == [False, True]
    rows[1].click()  # a click on the chosen row unchecks it, and the page checks it again
    assert home.scope == "starred" and rows[1].isChecked()


# The Doc Builder ----------------------------------------------------------------------------------------


@pytest.fixture
def builder(qt_app):
    pool = RenderPool(processes=0)
    page = DocBuilder(pool, 1.0)
    page.resize(1100, 700)
    page.show()
    yield page
    page.close_builder()
    page.close()
    page.deleteLater()
    pool.shutdown()


def test_the_builder_with_no_file_is_a_drop_zone_and_buttons_without_work_are_off(builder, sample_pdf):
    assert builder.empty.isVisibleTo(builder) and not builder.grid.isVisibleTo(builder)
    assert not builder.sort_button.isEnabled() and not builder.clear_button.isEnabled()
    assert not builder.order_hint.isVisibleTo(builder)
    builder.add_files([str(sample_pdf)])
    assert not builder.empty.isVisibleTo(builder) and builder.clear_button.isEnabled()
    assert not builder.sort_button.isEnabled() and not builder.order_hint.isVisibleTo(builder)  # one file: no order
    builder.add_files([str(sample_pdf)])
    assert builder.sort_button.isEnabled() and builder.order_hint.isVisibleTo(builder)
    builder.clear_all()
    assert builder.empty.isVisibleTo(builder) and not builder.clear_button.isEnabled()
    assert builder.order_hint.wordWrap()  # Russian runs past the panel


@pytest.mark.filterwarnings("ignore:Function.*QDragEnterEvent:DeprecationWarning")  # Qt's own, for the QPoint form
def test_pdf_files_dragged_over_the_builder_light_the_zone_that_takes_them(builder, sample_pdf):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(sample_pdf))])

    def drag_over():
        event = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                                Qt.KeyboardModifier.NoModifier)
        builder.dragEnterEvent(event)
        return event

    assert drag_over().isAccepted() and builder.empty._near.value == 1.0
    assert not builder.drop_over_cards.isVisibleTo(builder)  # nothing under it to dim
    builder.dragLeaveEvent(QDragLeaveEvent())
    assert builder.empty._near.value == 0.0
    builder.add_files([str(sample_pdf)])
    drag_over()
    assert builder.drop_over_cards.isVisibleTo(builder) and builder.empty._near.value == 0.0
    builder.dragLeaveEvent(QDragLeaveEvent())
    assert not builder.drop_over_cards.isVisibleTo(builder)
    other = QMimeData()
    other.setUrls([QUrl.fromLocalFile(str(sample_pdf.with_suffix(".txt")))])
    refused = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, other, Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier)
    builder.dragEnterEvent(refused)
    assert not refused.isAccepted() and builder.empty._near.value == 0.0  # only PDFs are welcome


def test_a_long_file_name_is_cut_in_the_sources_list_not_pushed_under_its_buttons(builder, sample_pdf, tmp_path):
    long = tmp_path / ("A file with a name that goes on and on and cannot possibly fit " * 2 + ".pdf")
    shutil.copy(sample_pdf, long)
    builder.add_files([str(long)])
    settle(50)
    row = builder.source_list.itemWidget(builder.source_list.item(0))
    name = row.findChildren(doc_builder._Elided)[0]
    assert "…" in name.text() and name.toolTip() == str(long)
    assert name.fontMetrics().horizontalAdvance(name.text()) <= name.width()


def test_the_cards_ask_for_the_pointer_themselves_and_do_not_wait_for_a_style_sheet_rule_to_do_it(builder):
    assert builder.grid.hasMouseTracking() and builder.grid.viewport().testAttribute(Qt.WidgetAttribute.WA_Hover)


def test_a_thumbnail_that_comes_in_fades_over_the_blank_page(builder, sample_pdf, motion_on):
    builder.add_files([str(sample_pdf)])
    source = builder.order[0]
    key = (source, 0)
    assert builder.thumb_level(key) == 1.0
    picture = QImage(60, 80, QImage.Format.Format_RGB32)
    picture.fill(QColor("white"))
    builder._on_rendered((builder._token, *key), 1.0, picture)
    assert builder.thumb_level(key) < 1.0
    assert wait_for(lambda: builder.thumb_level(key) == 1.0)
    assert key not in builder._arrived  # a finished fade is forgotten


# The markup library and the new-file panel ------------------------------------------------------------------


def test_the_markup_library_shows_its_hint_only_when_there_is_something_to_click(qt_app, tmp_path):
    library = Library(make_settings(tmp_path))
    panel = LibraryPanel(library, 1.0)
    assert panel.empty.isVisibleTo(panel) and not panel.hint.isVisibleTo(panel) and not panel.list.isVisibleTo(panel)
    library.add("Box", [Shape(page=0, rect=(0, 0, 40, 40))])
    assert not panel.empty.isVisibleTo(panel) and panel.hint.isVisibleTo(panel) and panel.list.isVisibleTo(panel)
    library.remove(0)
    assert panel.empty.isVisibleTo(panel) and not panel.hint.isVisibleTo(panel)
    assert panel.hint.wordWrap()  # in eleven of the fifteen languages it is longer than the panel is wide
    panel.deleteLater()


def test_the_small_buttons_of_a_panel_row_show_while_the_pointer_is_on_it(qt_app, tmp_path):
    panel = NewFilePanel(make_settings(tmp_path))
    row = next(row for row in panel.findChildren(Row) if row._buttons)  # a saved template: edit and delete
    buttons = [effect.parent() for effect in row._buttons]
    transparent = Qt.WidgetAttribute.WA_TransparentForMouseEvents
    assert [effect.opacity() for effect in row._buttons] == [0.0, 0.0]
    assert all(button.testAttribute(transparent) for button in buttons)  # unseen, they cannot be pressed
    QApplication.sendEvent(row, QEnterEvent(QPointF(5, 5), QPointF(5, 5), QPointF(5, 5)))
    assert [effect.opacity() for effect in row._buttons] == [1.0, 1.0]
    assert not any(button.testAttribute(transparent) for button in buttons)
    QApplication.sendEvent(row, QEvent(QEvent.Type.Leave))
    assert [effect.opacity() for effect in row._buttons] == [0.0, 0.0]
    assert all(button.testAttribute(transparent) for button in buttons)
    panel.deleteLater()
