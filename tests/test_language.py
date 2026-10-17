"""Zenith PDF in another language: the translations, how texts find them, and a window shown in Italian."""

import json
import re
import time

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QLabel, QTextBrowser

from zenith_pdf.app import create_app
from zenith_pdf.core.i18n import LANGUAGES, LOCALES, language, set_language, source, tr, tr_in
from zenith_pdf.core.platforms import native_keys
from zenith_pdf.settings import Settings
from zenith_pdf.ui.widgets import Segmented

FIELD = re.compile(r"\{(\d*)\}")


@pytest.fixture
def italian():
    set_language("it")
    yield
    set_language("en")


def test_every_language_has_its_file_and_keeps_the_fields():
    catalog = json.loads((LOCALES / "catalog.json").read_text(encoding="utf-8"))
    for code in LANGUAGES:
        if code == "en":
            continue
        table = json.loads((LOCALES / f"{code}.json").read_text(encoding="utf-8"))
        assert len(table) > 0.9 * len(catalog), code
        for key, value in table.items():
            assert len(FIELD.findall(key)) == len(FIELD.findall(value)), (code, key)
            assert "—" not in value and "–" not in value, (code, key)


def test_exact_texts_templates_and_parts(italian):
    assert language() == "it"
    assert tr("Settings") == "Impostazioni"
    assert tr("Saving page 3 (1 of 2)") == "Salvataggio della pagina 3 (1 di 2)"
    assert tr("12 pages") == "12 pagine"
    assert tr("Report pages") == "Report pages"  # one word of its own: the {} takes only a number
    # A part that is a known text is translated too.
    assert tr("Unlocked: goes back to Select after one use. Click to lock.") == \
        "Sbloccato: torna a Seleziona dopo un uso. Clicca per bloccare."
    assert source("Impostazioni") == "Settings"
    assert tr_in("glass", "Clear") == "Trasparente" and tr("Clear") == "Cancella"
    assert tr(None) is None and tr("") == ""


def test_every_guide_chapter_is_translated_quickly(qt_app):
    from PySide6.QtGui import QTextDocument

    from zenith_pdf.ui.guide import chapters

    for code in LANGUAGES:
        if code == "en":
            continue
        set_language(code)
        started = time.perf_counter()
        english = [key for key, _, _, page in chapters(QTextDocument()) if tr(page) == page]
        assert english == ["whats-new"], (code, english)  # that one is put together from translated parts
        assert time.perf_counter() - started < 5, code
    # A long text that fits no template fails fast, whatever templates of many {} it is tried against.
    started = time.perf_counter()
    assert tr("word, " * 3000 + "page") == "word, " * 3000 + "page"
    assert time.perf_counter() - started < 0.5
    set_language("en")


def test_english_is_left_as_it_is():
    set_language("en")
    assert tr("Settings") == "Settings" and tr_in("glass", "Clear") == "Clear"
    assert set_language("xx") == "en"


def test_a_window_in_italian(qt_app, tmp_path):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    store.setValue("language", "it")
    _app, window = create_app(["zenith-pdf"], Settings(store))
    try:
        assert language() == "it"
        window.resize(1200, 850)
        window.show()
        QTest.qWaitForWindowExposed(window)
        menu = window._build_menu()
        texts = [action.text().split("\t")[0] for action in menu.actions() if action.text()]
        assert "Impostazioni…" in texts and "Leggi ad alta voce" in texts
        untranslated = [text for text in texts if tr(source(text)) == source(text)]
        assert not untranslated, untranslated
        window.open_settings()
        QApplication.processEvents()
        page = window._settings_window
        labels = {label.text() for label in page.findChildren(QLabel)}
        assert {"Lingua", "Vetro", "Aggiornamenti"} <= labels
        glass = next(s for s in page.findChildren(Segmented) if s.items and s.items[0][0] == "clear")
        assert [b.text() for b in glass.buttons] == ["Trasparente", "Bilanciato", "Pieno"]
        nav = [page.nav.item(row).text() for row in range(page.nav.count())]
        assert "Vista e layout" in nav and "File e salvataggio" in nav
        window.open_guide("start")
        QApplication.processEvents()
        text = window._guide.findChild(QTextBrowser, "GuideText").toPlainText()
        assert "Zenith PDF legge, annota e riorganizza" in text and native_keys("Ctrl+Z") in text
    finally:
        if getattr(window, "_guide", None) is not None:
            window._guide.close()
        if window._settings_window is not None:
            window._settings_window.close()
        window.close()
        window.renderer.shutdown()
        set_language("en")
