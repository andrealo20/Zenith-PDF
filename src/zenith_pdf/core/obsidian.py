"""Study notes into an Obsidian vault: a Markdown note per document, each page a link that opens the PDF there.

The note is rewritten between two marker lines each time it is sent, so what the user adds above or below
them in Obsidian stays. Vaults are found in Obsidian's own list of them, read only.
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import quote

START = "<!-- zenith-pdf: notes start -->"
END = "<!-- zenith-pdf: notes end -->"


def _config_files() -> list[Path]:
    home = Path.home()
    if sys.platform == "win32":
        return [Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / "obsidian" / "obsidian.json"]
    if sys.platform == "darwin":
        return [home / "Library" / "Application Support" / "obsidian" / "obsidian.json"]
    config = Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    return [config / "obsidian" / "obsidian.json",
            home / ".var" / "app" / "md.obsidian.Obsidian" / "config" / "obsidian" / "obsidian.json"]


def vaults(files: list[Path] | None = None) -> list[Path]:
    """The vaults Obsidian knows, the one opened last first; only those still there."""
    found: list[tuple[float, Path]] = []
    for file in files if files is not None else _config_files():
        try:
            listed = json.loads(file.read_text(encoding="utf-8")).get("vaults", {})
        except (OSError, ValueError, AttributeError):
            continue
        for vault in listed.values() if isinstance(listed, dict) else []:
            path = Path(str(vault.get("path", "")))
            if str(path) and path.is_dir():
                found.append((float(vault.get("ts") or 0), path))
    seen, ordered = set(), []
    for _ts, path in sorted(found, key=lambda item: -item[0]):
        if path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def note_name(document: Path) -> str:
    """A file name Obsidian and every system accept, from the document's name."""
    name = re.sub(r'[\\/:*?"<>|#^\[\]]+', " ", document.stem).strip()
    return f"{' '.join(name.split()) or 'Document'}.md"


def note_body(notes: str, document: Path, link) -> str:
    """The part of the note Zenith PDF writes: the source and a link to open it, then the notes."""
    return "\n".join([START, f"Source: [{document.name}]({link(document, 1)})  ", f"Updated: {date.today()}", "",
                      notes.strip(), END])


def write_note(vault: Path, folder: str, document: Path, notes: str, link) -> Path:
    """Write the notes of `document` into `vault`/`folder`, keeping what the user wrote around them."""
    target = vault / folder.strip("/\\") / note_name(document) if folder.strip("/\\") else vault / note_name(document)
    target.parent.mkdir(parents=True, exist_ok=True)
    body = note_body(notes, document, link)
    if target.exists():
        old = target.read_text(encoding="utf-8")
        if START in old and END in old:
            before, rest = old.split(START, 1)
            after = rest.split(END, 1)[1]
            target.write_text(before + body + after, encoding="utf-8")
            return target
        target.write_text(old.rstrip() + "\n\n" + body + "\n", encoding="utf-8")
        return target
    front = "\n".join(["---", f'source: "{document}"', "tags: [zenith-pdf]", "---", ""])
    target.write_text(front + body + "\n", encoding="utf-8")
    return target


def open_uri(note: Path) -> str:
    """The link that shows `note` in Obsidian."""
    return "obsidian://open?path=" + quote(str(note), safe="")
