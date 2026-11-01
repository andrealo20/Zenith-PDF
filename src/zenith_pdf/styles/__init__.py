"""The styles of Settings > View & Layout > Style: each one a whole look of the app, with the same features.

A style is a theme.Style: its dark and light palettes, each carrying the style's theme.Look. Zenith's own lives
in theme.py; each of the others has a module here.
"""

from ..theme import ZENITH, Style
from . import dot, material, neon

STYLES: dict[str, Style] = {style.key: style for style in (ZENITH, dot.STYLE, neon.STYLE, material.STYLE)}
