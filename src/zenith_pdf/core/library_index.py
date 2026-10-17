"""An index of the text of every PDF opened, for searching all of them at once, offline.

SQLite's full-text search (FTS5), in one file in the cache folder: a row per page, with the words folded
(case and accents ignored). A file is read again only when its size or time changed. Reading uses PDFium,
one page at a time under its lock, so it can run in a thread beside the app.
"""

from __future__ import annotations

import re
import sqlite3
import threading
from dataclasses import dataclass
from pathlib import Path

from . import textclean
from .document import PDFIUM_LOCK
from .writing import clean_pdf_text

SNIPPET_WORDS = 14  # words of context around a match
MIN_QUERY = 2  # shorter words would match half the documents


@dataclass(frozen=True)
class Hit:
    path: Path
    page: int  # from 0
    snippet: str  # with the matched words between [ and ]


def query_terms(text: str) -> list[str]:
    """The words of a query, as FTS5 matches them: each a prefix, all of them on the same page."""
    return [word for word in re.findall(r"\w+", text.lower()) if len(word) >= MIN_QUERY]


class LibraryIndex:
    """The index file; safe to use from several threads, each with its own connection."""

    def __init__(self, path: Path):
        self.path = path
        self._local = threading.local()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS files (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, "
                       "pages INTEGER)")
            db.execute("CREATE VIRTUAL TABLE IF NOT EXISTS pages USING fts5(text, path UNINDEXED, page UNINDEXED, "
                       "tokenize='unicode61 remove_diacritics 2')")

    def _connect(self) -> sqlite3.Connection:
        db = getattr(self._local, "db", None)
        if db is None:
            db = self._local.db = sqlite3.connect(str(self.path), timeout=10)
        return db

    def close(self) -> None:
        db = getattr(self._local, "db", None)
        if db is not None:
            db.close()
            self._local.db = None

    # Writing ---------------------------------------------------------------------------------

    def needs(self, path: Path) -> bool:
        """Whether `path` is new to the index, or changed since it was read."""
        try:
            stat = path.stat()
        except OSError:
            return False
        row = self._connect().execute("SELECT mtime, size FROM files WHERE path = ?", (str(path),)).fetchone()
        return row is None or row[0] != stat.st_mtime or row[1] != stat.st_size

    def add(self, path: Path, stop=None) -> int:
        """Read `path` into the index (unless it hasn't changed): how many pages with words it had. A file that
        can't be read (gone, damaged, locked with a password) is remembered as empty until it changes."""
        path = Path(path).resolve()
        if not self.needs(path):
            return -1
        stat = path.stat()
        texts = self._read(path, stop)
        if texts is None:
            return -1  # stopped half way: nothing is written, the next pass reads it again
        db = self._connect()
        with db:
            db.execute("DELETE FROM pages WHERE path = ?", (str(path),))
            db.executemany("INSERT INTO pages (text, path, page) VALUES (?, ?, ?)",
                           [(text, str(path), index) for index, text in texts if text])
            db.execute("INSERT OR REPLACE INTO files (path, mtime, size, pages) VALUES (?, ?, ?, ?)",
                       (str(path), stat.st_mtime, stat.st_size, len(texts)))
        return sum(1 for _, text in texts if text)

    @staticmethod
    def _read(path: Path, stop=None) -> list[tuple[int, str]] | None:
        import pypdfium2 as pdfium

        texts = []
        try:
            with PDFIUM_LOCK:
                pdf = pdfium.PdfDocument(str(path))
                count = len(pdf)
            try:
                for index in range(count):
                    if stop is not None and stop():
                        return None
                    with PDFIUM_LOCK:
                        page = pdf[index]
                        textpage = page.get_textpage()
                        raw = textpage.get_text_range()
                        textpage.close()
                        page.close()
                    texts.append((index, clean_pdf_text(textclean.clean(raw))))
            finally:
                with PDFIUM_LOCK:
                    pdf.close()
        except (pdfium.PdfiumError, OSError):
            return []
        return texts

    def forget(self, path: Path) -> None:
        path = Path(path).resolve()
        db = self._connect()
        with db:
            db.execute("DELETE FROM pages WHERE path = ?", (str(path),))
            db.execute("DELETE FROM files WHERE path = ?", (str(path),))

    def clear(self) -> None:
        db = self._connect()
        with db:
            db.execute("DELETE FROM pages")
            db.execute("DELETE FROM files")

    # Reading ---------------------------------------------------------------------------------

    def search(self, text: str, limit: int = 60) -> list[Hit]:
        """The pages that hold every word of `text` (each also as the start of a longer word), best first."""
        terms = query_terms(text)
        if not terms:
            return []
        match = " AND ".join(f'"{term}"*' for term in terms)
        try:
            rows = self._connect().execute(
                "SELECT path, page, snippet(pages, 0, '[', ']', '…', ?) FROM pages WHERE pages MATCH ? "
                "ORDER BY bm25(pages) LIMIT ?", (SNIPPET_WORDS, match, limit)).fetchall()
        except sqlite3.Error:
            return []
        return [Hit(Path(path), int(page), " ".join(snippet.split())) for path, page, snippet in rows
                if Path(path).exists()]

    def files(self) -> int:
        return self._connect().execute("SELECT COUNT(*) FROM files WHERE pages > 0").fetchone()[0]
