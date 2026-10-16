"""The assistant (Ctrl+K), the tips a tool shows the first time it is used, and the writing helpers."""

import shutil

import pytest
from PySide6.QtCore import QPoint, QPointF, QSettings, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog

from zenith_pdf.app import create_app
from zenith_pdf.core import spelling, writing
from zenith_pdf.core.annotations import FreeText, Note, TextMarkup
from zenith_pdf.core.finder import Entry, Finder, distance, expand
from zenith_pdf.settings import Settings
from zenith_pdf.ui import writing_dialogs
from zenith_pdf.ui.context_menu import build_context_menu
from zenith_pdf.ui.dialogs import SHORTCUTS
from zenith_pdf.ui.help import TOPICS
from zenith_pdf.ui.tips import FIRST_TIP, TOOL_TIPS
from zenith_pdf.ui.tool_catalog import CATALOG

HAS_ENGLISH = spelling.available("en")
needs_english = pytest.mark.skipif(not HAS_ENGLISH, reason="Windows has no English dictionary here")


def settle(ms=30):
    QTest.qWait(ms)
    QApplication.processEvents()


# The search ---------------------------------------------------------------------------------

ENTRIES = [
    Entry("tool:signature", "tool", "Signature", "Tool · Insert", "tool", "Signs the document with a signature."),
    Entry("cmd:rotate", "command", "Rotate page 1 clockwise", "Command · This page", "rotate page turn right"),
    Entry("cmd:manager", "command", "Page manager: reorder, rotate, delete pages", "Command", "page manager"),
    Entry("tool:highlight", "tool", "Text highlight", "Tool · Review", "tool"),
    Entry("tool:marker", "tool", "Highlighter", "Tool · Free-form", "tool"),
    Entry("pdf:compress", "command", "Compress", "PDF tools · Document", "pdf tools", "A smaller file for sharing"),
    Entry("cmd:theme_dark", "command", "Theme: Dark", "Command", "theme colours appearance"),
    Entry("cmd:theme_light", "command", "Theme: Light", "Command", "theme colours appearance"),
    Entry("cmd:save", "command", "Save", "Command"),
    Entry("cmd:open", "command", "Open a PDF", "Command", "file document browse"),
    Entry("cmd:shortcuts", "command", "Keyboard shortcuts", "Command", "shortcut keys keyboard"),
    Entry("cmd:reading", "command", "Reading mode: only the pages", "Command", "reading focus hide bars"),
    Entry("tool:distance", "tool", "Distance", "Tool · Measure", "tool", "Measures the length of a line."),
]


@pytest.mark.parametrize("query, wanted", [
    ("signature", {"tool:signature"}),
    ("firma", {"tool:signature"}),
    ("come si firma un pdf", {"tool:signature"}),
    ("how do I add a signature", {"tool:signature"}),
    ("unterschrift", {"tool:signature"}),
    ("ruota pagina", {"cmd:rotate"}),
    ("Seite drehen", {"cmd:rotate"}),
    ("pivoter la page", {"cmd:rotate"}),
    ("girar página", {"cmd:rotate"}),
    ("comprimir", {"pdf:compress"}),
    ("komprimieren", {"pdf:compress"}),
    ("tema scuro", {"cmd:theme_dark"}),
    ("guardar", {"cmd:save"}),
    ("salvare", {"cmd:save"}),
    ("scorciatoie", {"cmd:shortcuts"}),
    ("modalità lettura", {"cmd:reading"}),
    ("dove trovo il righello", {"tool:distance"}),
    ("highligth", {"tool:highlight", "tool:marker"}),  # a typo
    ("evidenziatore", {"tool:highlight", "tool:marker"}),
    ("rot", {"cmd:rotate", "cmd:manager"}),  # typed so far
])
def test_questions_in_six_languages_find_the_right_thing(query, wanted):
    found = Finder(ENTRIES).search(query, 5)
    assert found, query
    assert found[0][0].key in wanted, (query, [(e.key, round(s, 2)) for e, s in found])


def test_nothing_is_found_for_nothing():
    finder = Finder(ENTRIES)
    assert finder.search("") == []
    assert finder.search("   ") == []
    assert finder.search("xqzv wplk") == []


def test_the_typo_distance_and_the_lexicon():
    assert distance("highlight", "highligth") == 1  # two letters swapped count once
    assert distance("rotate", "rotate") == 0
    assert distance("abc", "xyz", limit=2) == 3
    assert "rotate" in expand("ruotare")  # a form the lexicon lacks, from its stem
    assert "signature" in expand("assinatura")


def test_recent_picks_come_first():
    finder = Finder(ENTRIES)
    plain = [e.key for e, _ in finder.search("theme")]
    boosted = [e.key for e, _ in finder.search("theme", boost={"cmd:theme_light": 0.5})]
    assert boosted[0] == "cmd:theme_light" and set(plain[:2]) == {"cmd:theme_dark", "cmd:theme_light"}


# Writing helpers ----------------------------------------------------------------------------


@pytest.mark.parametrize("text, language", [
    ("The cat is on the table and it is happy with the sun.", "en"),
    ("Il gatto è sul tavolo e non vuole scendere per la cena.", "it"),
    ("Le chat est sur la table et il ne veut pas descendre.", "fr"),
    ("El gato está en la mesa y no quiere bajar para la cena.", "es"),
    ("Die Katze ist auf dem Tisch und will nicht zum Essen kommen.", "de"),
    ("O gato está na mesa e não quer descer para o jantar.", "pt"),
])
def test_the_language_of_a_text(text, language):
    assert writing.detect_language(text) == language


def test_tidy_fixes_spacing_punctuation_and_capitals():
    text = "this is  a small test ,with a few mistakes .then it ends here."
    fixed = writing.apply_fixes(text, writing.tidy(text, "en"))
    assert fixed == "This is a small test, with a few mistakes. then it ends here."
    assert writing.tidy("x = 3", "en") == []  # a label keeps its small letters
    assert writing.tidy("See report.pdf and www.example.org", "en") == []
    french = "Bonjour : comment allez-vous ?"
    assert writing.apply_fixes(french, writing.tidy(french, "fr")) == french  # French keeps its spaces


def test_key_sentences_keep_their_order_and_skip_abbreviations():
    text = ("Solar panels turn sunlight into electricity. Panels on a roof feed the house first. "
            "The weather was nice that day. Electricity the house does not use goes to the grid. "
            "My neighbour has a red car. Sunlight varies over the year, so panels make more electricity in summer. "
            "We had pasta for lunch. A battery stores electricity from the panels for the night.")
    picked = writing.key_sentences(text, 3, "en")
    assert len(picked) == 3
    positions = [text.index(s) for s in picked]
    assert positions == sorted(positions)
    assert "pasta" not in " ".join(picked) and "red car" not in " ".join(picked)
    assert len(writing.sentences("See fig. 3 for details. It shows e.g. the curve.")) == 2
    assert writing.clean_pdf_text("electri-\ncity runs\non") == "electricity runs on"


def test_study_notes_by_page_and_by_colour():
    marks = [writing.Marked(1, "Highlight", (1.0, 0.85, 0.0), "the second fact", "check this", 300.0),
             writing.Marked(0, "Underline", (0.9, 0.15, 0.15), "the first fact", "", 100.0)]
    by_page = writing.study_notes(marks, "Notes")
    assert by_page.index("## Page 1") < by_page.index("## Page 2")
    assert "**Red underline:** the first fact" in by_page and "  - Note: check this" in by_page
    by_colour = writing.study_notes(marks, "Notes", by="colour")
    assert "## Yellow" in by_colour and "- p. 2: the second fact" in by_colour
    assert writing.colour_name((0.2, 0.5, 0.95)) == "Blue"


@needs_english
def test_windows_spelling_flags_typos_and_doubled_words():
    issues = spelling.check("Teh cat sat on the the mat.", "en")
    words = {i.word: i for i in issues}
    assert words["Teh"].suggestions[0] == "The"
    assert any(i.action == "delete" for i in issues)
    changes = writing_dialogs.proposed_changes("Teh cat sat on the the mat .", "en")
    spans = [(c.start, c.end) for c in changes]
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:]))  # never two on the same characters


def test_text_without_a_dictionary_still_gets_the_tidy_fixes(monkeypatch):
    monkeypatch.setattr(spelling, "check", lambda text, language, limit=200: [])
    changes = writing_dialogs.proposed_changes("bonjour  le monde , il fait beau", "fr")
    assert {c.reason for c in changes} >= {"Extra space", "Space before punctuation"}


# The app ------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    settings = Settings(store)
    settings.set("remember_position", False)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1300, 900)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


@pytest.fixture
def workspace(window, sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    window.open_file(path)
    workspace = window.current_workspace()
    settle()
    yield workspace
    workspace.tool_settings.set_tool("select")
    if window.assistant_open():
        window._assistant.close()
    if workspace in window.workspaces:
        window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_ctrl_k_opens_the_assistant_and_enter_runs_the_best_match(window, workspace):
    QTest.keyClick(window, Qt.Key.Key_K, Qt.KeyboardModifier.ControlModifier)
    settle()
    assert window.assistant_open()
    panel = window._assistant
    panel.search.setText("rettangolo")
    QTest.keyClick(panel.search, Qt.Key.Key_Return)
    settle(60)
    assert not window.assistant_open()
    assert workspace.tool_settings.tool == "rectangle"
    assert window.settings.get("assistant_recent")[0] == "tool:rectangle"


def test_the_assistant_without_a_question_suggests_and_remembers(window, workspace):
    window.settings.set("assistant_recent", ["cmd:fit_page"])
    window.show_assistant()
    panel = window._assistant
    headings = [panel.list.itemWidget(panel.list.item(r)).text() for r in range(panel.list.count())
                if not panel.list.item(r).flags() & Qt.ItemFlag.ItemIsSelectable]
    assert "Recent" in headings and "Try" in headings
    assert panel.current_key() is not None  # the first result is ready for Enter
    panel.search.setText("zzzz qqqq")
    assert panel.current_key() is None  # nothing matched, and it says so
    panel.close()


def test_what_cant_run_now_says_why(window, workspace):
    window.show_assistant()
    panel = window._assistant
    assert not panel.run("write:summary_selection")  # no text selected
    assert "Select some text" in panel.footer.text()
    assert not panel.run("write:model")
    assert "language model" in panel.footer.text()
    assert window.assistant_open()
    panel.close()


def test_a_setting_found_opens_lit_in_its_category(window, workspace):
    window.show_assistant()
    panel = window._assistant
    panel.search.setText("glass")
    assert panel.current_key() == "set:view:Glass"
    QTest.keyClick(panel.search, Qt.Key.Key_Return)
    settle(80)
    settings_window = window.settings_window()
    assert settings_window.isVisible()
    assert settings_window.sections["view"].isVisible() and not settings_window.sections["files"].isVisible()
    row = next(r for r in settings_window.sections["view"].rows if r.title == "Glass")
    assert row.property("flash") is True
    settings_window.hide()


def test_every_tool_has_tips_written_like_the_rest():
    assert set(TOOL_TIPS) == {spec.key for spec in CATALOG}
    for lines in TOOL_TIPS.values():
        assert 1 <= len(lines) <= 3
        assert all(chr(0x2014) not in line and chr(0x2013) not in line for line in lines)  # no long dashes


def test_a_tool_shows_its_tips_once(window, workspace):
    settings = window.settings
    settings.set("tool_tips", True)
    settings.set("tips_seen", [])
    tips = workspace.tool_tips
    workspace.tool_settings.set_tool("rectangle")
    settle()
    card = tips.card
    assert card.isVisible() and card.title.text() == "Tips: Rectangle"
    assert any(FIRST_TIP in label.text() for label in card.lines)  # the very first card says where to ask
    assert "rectangle" in settings.get("tips_seen")
    toolbar_bottom = workspace.toolbar.geometry().bottom()
    assert card.geometry().top() > toolbar_bottom
    workspace.tool_settings.set_tool("ellipse")
    settle()
    assert card.isVisible() and card.title.text() == "Tips: Ellipse"
    assert not any(FIRST_TIP in label.text() for label in card.lines)
    QTest.mousePress(workspace.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                     QPoint(300, 400))
    QTest.mouseRelease(workspace.view.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier,
                       QPoint(300, 400))
    assert not card.isVisible()  # using the tool on the page puts it away
    workspace.tool_settings.set_tool("rectangle")
    settle()
    assert not card.isVisible()  # once ever


def test_tips_stay_away_when_turned_off_or_on_a_hand_over(window, workspace):
    settings = window.settings
    settings.set("tool_tips", True)
    settings.set("tips_seen", [])
    tools = workspace.tool_settings
    tools.handing_over = True
    try:
        tools.set_tool("hand")
    finally:
        tools.handing_over = False
    assert not workspace.tool_tips.card.isVisible()  # nobody picked Pan
    workspace.tool_tips.card.off_button.click()
    assert settings.get("tool_tips") is False
    tools.set_tool("pen")
    settle()
    assert not workspace.tool_tips.card.isVisible()
    settings.set("tool_tips", True)


@needs_english
def test_check_spelling_fixes_a_text_box_in_one_undo_step(window, workspace, monkeypatch):
    view = workspace.view
    box = FreeText(page=0, rect=(72.0, 200.0, 220.0, 40.0), contents="Teh cat sat on the the mat .")
    view.add_annotations([box], "Text box")
    view.tool_settings.set_tool("select")
    view.select([box])

    def accept(dialog):
        dialog.choice = "apply"
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(writing_dialogs.SpellingDialog, "exec", accept)
    assert writing_dialogs.check_spelling(view)
    assert workspace.store.get(box.id).contents == "The cat sat on the mat."
    workspace.undo_stack.undo()
    assert workspace.store.get(box.id).contents == "Teh cat sat on the the mat ."


def test_spelling_is_in_the_menu_of_text_boxes_and_notes(window, workspace):
    view = workspace.view
    note = Note(page=0, position=(100.0, 100.0), contents="A note")
    for annotation in (FreeText(page=0, rect=(72.0, 72.0, 100.0, 30.0), contents="Text"), note):
        menu = build_context_menu(view, 0, QPointF(80, 80), annotation)
        assert "Check spelling…" in [a.text() for a in menu.actions()]
        menu.deleteLater()


def test_key_points_become_a_note(window, workspace, monkeypatch):
    view = workspace.view
    text = ("Solar panels turn sunlight into electricity. Panels on a roof feed the house first. "
            "Electricity the house does not use goes to the grid. A battery stores electricity for the night.")

    def keep(dialog):
        dialog.choice = "note"
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(writing_dialogs.SummaryDialog, "exec", keep)
    before = len(workspace.store.all())
    assert writing_dialogs.summarise(view, text, 0, (500.0, 500.0), "the text") == "note"
    notes = [a for a in workspace.store.all() if isinstance(a, Note)]
    assert len(workspace.store.all()) == before + 1
    assert notes[-1].contents.startswith("Key points\n• ")
    x, y = notes[-1].position
    width, height = workspace.document.page_size(0)
    assert 0 <= x <= width and 0 <= y <= height  # kept on the page


def test_study_notes_gather_the_marked_text(window, workspace, monkeypatch):
    view = workspace.view
    hit = next(h for h in workspace.document.iter_search("needle", False, False) if h.page == 0)
    view.add_annotations([TextMarkup(page=0, quads=hit.rects, color=(1.0, 0.85, 0.0), contents="key word")],
                         "Highlight")
    seen = {}

    def look(dialog):
        seen["markdown"] = dialog.markdown
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(writing_dialogs.StudyNotesDialog, "exec", look)
    writing_dialogs.study_notes(view)
    assert "needle" in seen["markdown"] and "Note: key word" in seen["markdown"]


def test_the_assistant_is_explained_and_has_its_shortcut():
    assert "assistant" in TOPICS
    assert any(keys == "Ctrl+K" for _, entries in SHORTCUTS for keys, _ in entries)
