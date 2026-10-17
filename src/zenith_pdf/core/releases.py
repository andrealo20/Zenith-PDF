"""What each version brought: the guide's What's new chapter and the tour shown after an update.

A highlight's `action` says what its Show me button does:
  "tool:<key>"           pick that tool
  "setting:<category>"   open Settings at that category
  "guide:<chapter>"      open the guide at that chapter
  "command:<name>"       run a command of the window (see MainWindow.show_me)
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import __version__


@dataclass(frozen=True)
class Highlight:
    title: str
    text: str
    icon: str
    action: str = ""


@dataclass(frozen=True)
class Release:
    version: str
    highlights: tuple[Highlight, ...]
    more: tuple[str, ...] = ()


RELEASES = (
    Release("0.5.0", (
        Highlight("A pen that feels the pressure", "With a pen on a Surface or a Wacom tablet, the line follows how hard "
                  "you press. A hand resting on the screen draws nothing while the pen is near, and the pen's button "
                  "or its other end rubs out.", "pen-nib", "tool:pen"),
        Highlight("Glass that follows the page", "The bars turn light over a bright page and dark over the dark canvas, "
                  "as Liquid Glass does, so they stay clear in both themes.", "drop-half", "setting:view"),
        Highlight("Zenith PDF in your language", "Menus, settings, help and the guide in English, Italian, French, "
                  "Spanish, German or Portuguese. It follows the system's language, or the one you pick.", "translate",
                  "setting:view"),
        Highlight("Search in every PDF", "The home page searches inside all the documents you opened, offline, and "
                  "opens the one you pick at the page the words are on.", "magnifying-glass", "command:search_all"),
        Highlight("Fill in forms", "Text fields, check boxes, radio buttons and lists of PDF forms can be filled in and "
                  "saved, and undone like any change.", "check-square-offset", "guide:forms"),
        Highlight("Notes to Obsidian", "Study notes go straight into your Obsidian vault, each with a link that opens "
                  "the PDF at its page.", "notebook", "command:study_notes"),
        Highlight("Flashcards", "The highlights of a colour become cards to study here or to take to Anki.", "cards",
                  "command:flashcards"),
        Highlight("Read aloud", "Zenith PDF reads the page or the selected text with the system's voices, following "
                  "each sentence on the page.", "speaker-high", "command:read_aloud"),
        Highlight("Compare two versions", "Two versions of a PDF side by side, with what was added, removed or changed "
                  "marked on both.", "git-diff", "command:compare"),
        Highlight("Updates by themselves", "Zenith PDF tells you when a new version is out, and can install it on its "
                  "own. After each update this tour shows what changed.", "arrow-circle-up", "setting:help"),
    ), (
        "Opening a PDF while Zenith PDF runs adds it to the open window instead of starting a second copy.",
        "Icons in colour by what they do, with a touch of depth, as in Acrobat's list of tools.",
        "A tool that is not locked goes back to Select after one use, with what it made selected.",
        "Smaller downloads for macOS and Linux.",
    )),
    Release("0.4.0", (
        Highlight("Windows, macOS and Linux", "The same app on the three systems, with each one's text recognition, "
                  "spell checker, fonts and keys.", "desktop"),
        Highlight("An installer for Windows", "Choose the folder and a desktop shortcut; Zenith PDF joins the Start "
                  "menu and Open with for PDF files.", "download-simple"),
        Highlight("Clearer glass", "The Clear glass lets the page show through, with a rim of light like Apple's "
                  "Liquid Glass.", "drop", "setting:view"),
    )),
    Release("0.3.0", (
        Highlight("An assistant", "Ctrl+K finds any tool, command, setting or shortcut from a few words, in six "
                  "languages, with no internet.", "sparkle", "command:assistant"),
        Highlight("Writing help", "Spelling and punctuation for text boxes and notes, key sentences, and study notes "
                  "from your highlights.", "pencil-simple"),
        Highlight("Several windows", "Drag a tab out of the strip and the document opens in a window of its own.",
                  "app-window"),
    ), (
        "Every tool has its own menu, with a live preview, its lock, a subject and a pin to keep it open.",
        "Audio notes recorded from the microphone, or attached from a file.",
        "A tool chooser with over thirty tools, and a toolbar you arrange yourself.",
        "A home page with every file, stars, tags and pinned folders; reading mode; links with Back and Forward.",
    )),
)
EARLIER = (
    "Settings with search, help cards on every button, pictures pasted with Ctrl+V.",
    "PDF tools: page numbers, crop, page size, pages per sheet, split, blank pages, export, compress, passwords, "
    "sanitize, properties, attachments, repair; redaction.",
    "The viewer, annotations, page management, text recognition, the Doc Builder, glass bars.",
)


def version_tuple(text: str) -> tuple[int, ...]:
    """"0.4.10" as (0, 4, 10), for comparing; anything that is not a number counts as 0."""
    parts = []
    for part in str(text).strip().lstrip("vV").split("."):
        digits = "".join(ch for ch in part if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts)


def since(seen: str, current: str = __version__) -> list[Release]:
    """The releases after `seen` up to `current`, the newest first."""
    low, high = version_tuple(seen), version_tuple(current)
    return [release for release in RELEASES if low < version_tuple(release.version) <= high]
