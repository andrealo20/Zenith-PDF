"""Telling the author about a problem: GitHub's page for a new issue, filled in with what the user wrote and what
this copy of Zenith PDF is. Nothing leaves the app by itself: the browser opens, and the user sends it from there."""

from __future__ import annotations

import platform
import sys
from urllib.parse import urlencode

from .. import __version__
from .i18n import language
from .platforms import package_family
from .updates import REPOSITORY, install_kind

NEW_ISSUE = f"https://github.com/{REPOSITORY}/issues/new"
MOST = 4000  # characters of what the user wrote: the whole address stays within what every browser takes


def where_from() -> str:
    """How this copy was installed."""
    if package_family():
        return "Microsoft Store"
    if not getattr(sys, "frozen", False):
        return "source"
    return {"installer": "installer", "appimage": "AppImage", "bundle": "app bundle"}.get(install_kind(), "download")


def details() -> str:
    """What this copy is and runs on, for the report: no file, folder or user names."""
    from PySide6 import __version__ as pyside

    system = f"{platform.system()} {platform.release()} ({platform.version()})"
    return f"Zenith PDF {__version__} ({where_from()}), {system}, Qt {pyside}, language {language()}"


def issue_url(description: str, about: str) -> str:
    """GitHub's new issue page, with `description` (its first line the title) and `about` this copy."""
    text = description.strip()
    if len(text) > MOST:
        text = text[:MOST] + "…"
    title = text.splitlines()[0][:80] if text else "A problem"
    return NEW_ISSUE + "?" + urlencode({"title": title, "body": f"{text}\n\n{about}\n", "labels": "bug"})
