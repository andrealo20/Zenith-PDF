"""Reading aloud with the system's voices, through Qt's text to speech: offline, one sentence at a time.

The page is read from its first sentence, and on to the next pages; selected text is read on its own. The
sentence being read is lit on the page and kept in sight. A small bar over the bottom of the pages has
Pause, the sentences before and after, Stop, the speed and the voice.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QLocale, QObject, QPoint, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import QGraphicsRectItem, QHBoxLayout, QMenu

from ..core import textclean, writing
from ..settings import Settings
from ..theme import themes
from .widgets import FloatingBar, IconButton, separator

SPEEDS = (0.75, 1.0, 1.25, 1.5, 2.0)  # what the speed menu offers, times the voice's own pace
LANGUAGE_LOCALES = {"en": "en_US", "it": "it_IT", "fr": "fr_FR", "es": "es_ES", "de": "de_DE", "pt": "pt_PT"}


def available() -> bool:
    try:
        from PySide6.QtTextToSpeech import QTextToSpeech
    except ImportError:
        return False
    return bool([engine for engine in QTextToSpeech.availableEngines() if engine != "mock"])


def rate_for(speed: float) -> float:
    """Qt's rate (-1 to 1, 0 the voice's own pace) for a speed: 0.5 is half as fast, 2 twice."""
    return max(-1.0, min(1.0, math.log2(speed)))


class Sentence:
    """A sentence to read: its text, and where it is (page and display-space rectangles), if known."""

    def __init__(self, text: str, page: int = -1, rects: tuple = ()):
        self.text, self.page, self.rects = text, page, rects


def page_sentences(document, index: int) -> list[Sentence]:
    """The sentences of a page, each with the rectangles of its lines."""
    text, aligned = document.page_characters(index)
    found = []
    for start, end in writing.sentence_spans(text):
        spoken = writing.clean_pdf_text(textclean.clean(text[start:end]))
        if not any(char.isalpha() for char in spoken):
            continue  # a page number, a rule of dashes: no words
        rects = document.text_span(index, start, end - 1)[0] if aligned else ()
        found.append(Sentence(spoken, index, rects))
    return found


class Reader(QObject):
    """Reads sentences, page after page. `speaking` carries the sentence being read (None when done)."""

    speaking = Signal(object)
    state_changed = Signal(str)  # "playing", "paused" or "stopped"

    def __init__(self, document, settings: Settings | None, engine: str = "", parent: QObject | None = None):
        super().__init__(parent)
        from PySide6.QtTextToSpeech import QTextToSpeech

        self.document = document
        self._settings = settings
        self.tts = QTextToSpeech(engine, self) if engine else QTextToSpeech(self)
        self.tts.stateChanged.connect(self._state)
        self.queue: list[Sentence] = []
        self.current = -1
        self.state = "stopped"
        self.follow_pages = False  # read on into the next pages when the queue runs out
        self._live = False  # a sentence is being said, and its end moves on to the next
        self._heard = False  # the engine has said it started on it
        self._page = -1
        self.set_speed(float(self._get("read_speed") or 1.0))

    def _get(self, key: str):
        return self._settings.get(key) if self._settings is not None else None

    def _set(self, key: str, value) -> None:
        if self._settings is not None:
            self._settings.set(key, value)

    # What to read -------------------------------------------------------------------------

    def read_from_page(self, page: int) -> None:
        self.follow_pages = True
        self._load(page_sentences(self.document, page), page)

    def read_text(self, text: str, page: int = -1, rects: tuple = ()) -> None:
        """Selected text: its sentences, the selection lit as a whole while they are read."""
        self.follow_pages = False
        self._load([Sentence(sentence, page, rects) for sentence in writing.sentences(text)], page)

    def _load(self, sentences: list[Sentence], page: int) -> None:
        self._quiet()
        self.queue, self.current, self._page = sentences, -1, page
        if not sentences:
            if self.follow_pages and page + 1 < self.document.page_count:
                self.read_from_page(page + 1)  # a page with nothing to read: the next one
            else:
                self._finish()
            return
        self._choose_voice(" ".join(s.text for s in sentences[:8]))
        self._set_state("playing")
        self._say(0)

    # The engine ---------------------------------------------------------------------------
    # One sentence at a time: every engine says when a sentence is over (Ready after Speaking), while what
    # they report for a queue of them differs. A Ready only counts after the Speaking of the same sentence,
    # so the one a stop sends, late, never skips the sentence said next.

    def _say(self, index: int) -> None:
        self.current = index
        self._heard = False
        self._live = True
        self.speaking.emit(self.queue[index])
        self.tts.say(self.queue[index].text)

    def _quiet(self) -> None:
        """Stop the engine without its Ready counting as a sentence done."""
        self._live = False
        self.tts.stop()

    def _state(self, state) -> None:
        from PySide6.QtTextToSpeech import QTextToSpeech

        if state == QTextToSpeech.State.Speaking:
            self._heard = True
        elif state == QTextToSpeech.State.Ready and self._live and self._heard and self.state == "playing":
            self._live = False
            QTimer.singleShot(0, self._advance)  # not from inside the engine's own signal
        elif state == QTextToSpeech.State.Error:
            self._finish()

    def _advance(self) -> None:
        if self.state != "playing":
            return
        if self.current + 1 < len(self.queue):
            self._say(self.current + 1)
        elif self.follow_pages and self._page + 1 < self.document.page_count:
            self.read_from_page(self._page + 1)
        else:
            self._finish()

    def _finish(self) -> None:
        self._quiet()
        self.queue, self.current = [], -1
        self._set_state("stopped")
        self.speaking.emit(None)

    def _set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            self.state_changed.emit(state)

    # Controls -----------------------------------------------------------------------------

    def toggle(self) -> None:
        if self.state == "playing":
            self.tts.pause()
            self._set_state("paused")
        elif self.state == "paused":
            self.tts.resume()
            self._set_state("playing")

    def step(self, by: int) -> None:
        """The sentence `by` places away: -1 the one before (or this one again, from its start), +1 the next."""
        if not self.queue:
            return
        target = min(max(self.current + by, 0), len(self.queue) - 1)
        self._quiet()
        self._set_state("playing")
        self._say(target)

    def stop(self) -> None:
        if self.state != "stopped":
            self._finish()

    def set_speed(self, speed: float) -> None:
        self.speed = speed
        self.tts.setRate(rate_for(speed))
        self._set("read_speed", speed)

    def voices(self) -> list:
        return list(self.tts.availableVoices())

    def set_voice(self, voice) -> None:
        self.tts.setVoice(voice)
        self._set("read_voice", voice.name())

    def _choose_voice(self, sample: str) -> None:
        """The voice of the text's language: the one chosen before if it speaks it, else the system's first."""
        language = writing.detect_language(sample, "en")
        locale = QLocale(LANGUAGE_LOCALES.get(language, "en_US"))
        if locale.language() != self.tts.locale().language() and locale in self.tts.availableLocales():
            self.tts.setLocale(locale)
        wanted = self._get("read_voice")
        for voice in self.tts.availableVoices():
            if voice.name() == wanted:
                self.tts.setVoice(voice)
                break


class ReadAloudBar(FloatingBar):
    """The controls over the bottom of the pages, while a reading goes on."""

    def __init__(self, reader: Reader, parent=None):
        super().__init__(parent)
        self.reader = reader
        self.previous = IconButton("skip-back", "The sentence before", icon_size=18)
        self.previous.clicked.connect(lambda: reader.step(-1))
        self.play = IconButton("pause", "Pause", icon_size=18)
        self.play.clicked.connect(reader.toggle)
        self.next = IconButton("skip-forward", "The next sentence", icon_size=18)
        self.next.clicked.connect(lambda: reader.step(1))
        self.speed = IconButton("gauge", "Speed", icon_size=18)
        self.speed.clicked.connect(self._speed_menu)
        self.voice = IconButton("user", "Voice", icon_size=18)
        self.voice.clicked.connect(self._voice_menu)
        self.stop = IconButton("stop", "Stop reading", icon_size=18)
        self.stop.clicked.connect(reader.stop)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 4, 8, 4)
        layout.setSpacing(4)
        for widget in (self.previous, self.play, self.next, separator(), self.speed, self.voice, separator(),
                       self.stop):
            layout.addWidget(widget)
        reader.state_changed.connect(self._state)
        self.hide()

    def _state(self, state: str) -> None:
        self.play.set_icon_name("play" if state == "paused" else "pause")
        self.play.setToolTip("Go on" if state == "paused" else "Pause")

    def _speed_menu(self) -> None:
        menu = QMenu(self)
        for speed in SPEEDS:
            action = menu.addAction(f"{speed:g}×")
            action.setCheckable(True)
            action.setChecked(abs(speed - self.reader.speed) < 0.01)
            action.triggered.connect(lambda _=False, s=speed: self.reader.set_speed(s))
        menu.exec(self.speed.mapToGlobal(QPoint(0, -menu.sizeHint().height())))
        menu.deleteLater()

    def _voice_menu(self) -> None:
        menu = QMenu(self)
        current = self.reader.tts.voice().name()
        voices = self.reader.voices()
        if not voices:
            menu.addAction("No other voice for this language").setEnabled(False)
        for voice in voices:
            action = menu.addAction(voice.name())
            action.setCheckable(True)
            action.setChecked(voice.name() == current)
            action.triggered.connect(lambda _=False, v=voice: self.reader.set_voice(v))
        menu.exec(self.voice.mapToGlobal(QPoint(0, -menu.sizeHint().height())))
        menu.deleteLater()


class ReadAloud(QObject):
    """A workspace's reading: the Reader, its bar, and the light on the sentence being read."""

    def __init__(self, workspace, engine: str = ""):
        super().__init__(workspace)
        self.workspace = workspace
        self.reader = Reader(workspace.document, workspace.settings, engine, self)
        self.bar = ReadAloudBar(self.reader)
        workspace.canvas.add_floating(self.bar)
        self.bar.bottom_bar = True
        self._lit: list[QGraphicsRectItem] = []
        self.reader.speaking.connect(self._light)
        self.reader.state_changed.connect(self._state)

    def start(self) -> None:
        view = self.workspace.view
        selection = view.text_selection()
        if selection is not None:
            page, rects, text = selection
            self.reader.read_text(text, page, tuple(rects))
        else:
            self.reader.read_from_page(view.current_page)

    def _state(self, state: str) -> None:
        if state == "stopped":
            self.bar.hide()
        else:
            self.workspace.canvas.place_bottom(self.bar)

    def _light(self, sentence: Sentence | None) -> None:
        view = self.workspace.view
        for item in self._lit:
            if item.scene() is not None:
                item.scene().removeItem(item)
        self._lit = []
        if sentence is None or sentence.page < 0 or not sentence.rects:
            return
        page = view._pages[sentence.page]
        colour = QColor(themes.current.accent)
        colour.setAlpha(70)
        area = QRectF()
        for x, y, w, h in sentence.rects:
            item = QGraphicsRectItem(x - 1, y - 1, w + 2, h + 2, page)
            item.setPen(Qt.PenStyle.NoPen)
            item.setBrush(QBrush(colour))
            item.setZValue(1)
            self._lit.append(item)
            area = area.united(item.sceneBoundingRect())
        view.ensureVisible(area, 80, 160)

    def stop(self) -> None:
        self.reader.stop()
