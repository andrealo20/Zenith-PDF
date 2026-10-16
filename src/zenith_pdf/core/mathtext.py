"""A small renderer for LaTeX-style equations, enough for notes and homework.

It understands the common part of TeX math: superscripts and subscripts,
\\frac, \\sqrt (with an index), Greek letters and the usual symbols, \\sum,
\\prod and \\int with limits, \\left( ... \\right) delimiters that grow with
their content, function names like \\sin, \\text{...}, accents (\\hat, \\bar,
\\vec, \\dot) and spacing commands. The result is a picture, placed on the
page as an image annotation that keeps its source for later edits.

Layout works on boxes (width, ascent, descent) measured with Qt's fonts and
drawn with QPainter, the way TeX itself thinks about formulas.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QImage, QPainter, QPainterPath, QPen

MATH_FONT = "Cambria Math"
TEXT_FONT = "Cambria"
SCRIPT = 0.7  # a script's size, relative to its base
OVERSIZE = 4  # fonts are laid out at 4x and drawn at a quarter: Qt measures whole pixels
AXIS = 0.25  # height of the fraction bar and of + and -, in ems above the baseline

GREEK = {
    "alpha": "α", "beta": "β", "gamma": "γ", "delta": "δ", "epsilon": "ϵ", "varepsilon": "ε", "zeta": "ζ",
    "eta": "η", "theta": "θ", "vartheta": "ϑ", "iota": "ι", "kappa": "κ", "lambda": "λ", "mu": "μ", "nu": "ν",
    "xi": "ξ", "pi": "π", "varpi": "ϖ", "rho": "ρ", "varrho": "ϱ", "sigma": "σ", "varsigma": "ς", "tau": "τ",
    "upsilon": "υ", "phi": "ϕ", "varphi": "φ", "chi": "χ", "psi": "ψ", "omega": "ω",
    "Gamma": "Γ", "Delta": "Δ", "Theta": "Θ", "Lambda": "Λ", "Xi": "Ξ", "Pi": "Π", "Sigma": "Σ", "Upsilon": "Υ",
    "Phi": "Φ", "Psi": "Ψ", "Omega": "Ω",
}
RELATIONS = {
    "=": "=", "<": "<", ">": ">", "leq": "≤", "le": "≤", "geq": "≥", "ge": "≥", "neq": "≠", "ne": "≠",
    "approx": "≈", "equiv": "≡", "sim": "∼", "simeq": "≃", "propto": "∝", "in": "∈", "notin": "∉",
    "subset": "⊂", "supset": "⊃", "subseteq": "⊆", "supseteq": "⊇", "to": "→", "rightarrow": "→",
    "leftarrow": "←", "Rightarrow": "⇒", "Leftarrow": "⇐", "Leftrightarrow": "⇔", "leftrightarrow": "↔",
    "mapsto": "↦", "perp": "⊥", "parallel": "∥", "ll": "≪", "gg": "≫", "iff": "⟺", "implies": "⟹",
}
BINARY = {
    "+": "+", "-": "−", "*": "∗", "pm": "±", "mp": "∓", "times": "×", "cdot": "⋅", "div": "÷", "cup": "∪",
    "cap": "∩", "wedge": "∧", "vee": "∨", "oplus": "⊕", "otimes": "⊗", "circ": "∘", "star": "⋆", "setminus": "∖",
}
SYMBOLS = {
    "infty": "∞", "partial": "∂", "nabla": "∇", "forall": "∀", "exists": "∃", "emptyset": "∅", "neg": "¬",
    "ldots": "…", "cdots": "⋯", "vdots": "⋮", "ddots": "⋱", "degree": "°", "angle": "∠", "hbar": "ℏ", "ell": "ℓ",
    "Re": "ℜ", "Im": "ℑ", "prime": "′", "langle": "⟨", "rangle": "⟩", "{": "{", "}": "}", "|": "‖", "%": "%",
    "#": "#", "&": "&", "_": "_", "$": "$", "aleph": "ℵ", "triangle": "△", "square": "□", "checkmark": "✓",
}
BIG_OPERATORS = {"sum": "∑", "prod": "∏", "coprod": "∐", "int": "∫", "iint": "∬", "iiint": "∭", "oint": "∮",
                 "bigcup": "⋃", "bigcap": "⋂"}
INTEGRALS = {"int", "iint", "iiint", "oint"}
FUNCTIONS = {"sin", "cos", "tan", "cot", "sec", "csc", "arcsin", "arccos", "arctan", "sinh", "cosh", "tanh", "log",
             "ln", "lg", "exp", "det", "dim", "ker", "deg", "gcd", "arg", "max", "min", "sup", "inf", "lim", "Pr"}
LIMIT_FUNCTIONS = {"lim", "max", "min", "sup", "inf", "Pr"}
ACCENTS = {"hat": "^", "bar": "‾", "vec": "→", "dot": "˙", "ddot": "¨", "tilde": "~", "overline": None}
SPACES = {",": 0.17, ":": 0.22, ";": 0.28, " ": 0.25, "quad": 1.0, "qquad": 2.0, "!": -0.17}


class MathError(ValueError):
    pass


# Boxes ---------------------------------------------------------------------------


@dataclass
class Box:
    width: float = 0.0
    ascent: float = 0.0
    descent: float = 0.0

    def draw(self, painter: QPainter, x: float, baseline: float) -> None:
        pass


@dataclass
class Glyph(Box):
    text: str = ""
    font: QFont = field(default_factory=QFont)  # at OVERSIZE times the layout size

    def draw(self, painter, x, baseline):
        painter.save()
        painter.translate(x, baseline)
        painter.scale(1 / OVERSIZE, 1 / OVERSIZE)
        painter.setFont(self.font)
        painter.drawText(QPointF(0, 0), self.text)
        painter.restore()


@dataclass
class Row(Box):
    items: list = field(default_factory=list)  # (offset x, shift up, box)

    def draw(self, painter, x, baseline):
        for dx, up, box in self.items:
            box.draw(painter, x + dx, baseline - up)


@dataclass
class Rule(Box):
    """A filled rectangle above the baseline: fraction bars, overlines."""

    def draw(self, painter, x, baseline):
        painter.fillRect(QRectF(x, baseline - self.ascent, self.width, self.ascent + self.descent), painter.pen().color())


@dataclass
class Radical(Box):
    size: float = 10.0
    inner: Box = field(default_factory=Box)
    gap: float = 0.0

    def draw(self, painter, x, baseline):
        s = self.size
        top = baseline - self.ascent + s * 0.05
        bottom = baseline + self.descent
        path = QPainterPath(QPointF(x, baseline - self.inner.ascent * 0.45))
        path.lineTo(QPointF(x + s * 0.12, baseline - self.inner.ascent * 0.55))
        path.lineTo(QPointF(x + s * 0.3, bottom - s * 0.02))
        path.lineTo(QPointF(x + self.gap - s * 0.04, top))
        path.lineTo(QPointF(x + self.width, top))
        painter.save()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(painter.pen().color(), s * 0.06, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap,
                            Qt.PenJoinStyle.MiterJoin))
        painter.drawPath(path)
        painter.restore()
        self.inner.draw(painter, x + self.gap, baseline)


@dataclass
class Stretched(Box):
    """A delimiter grown to a height: ( ) [ ] { } | drawn from the font, stretched vertically."""

    text: str = "("
    font: QFont = field(default_factory=QFont)  # at OVERSIZE times the layout size
    natural: tuple = (0.0, 0.0)  # its own ascent and descent, at layout size

    def draw(self, painter, x, baseline):
        ascent, descent = self.natural
        factor = (self.ascent + self.descent) / max(ascent + descent, 1e-6)
        painter.save()
        painter.translate(x, baseline + self.descent)
        painter.scale(1 / OVERSIZE, factor / OVERSIZE)
        painter.setFont(self.font)
        painter.drawText(QPointF(0, -descent * OVERSIZE), self.text)
        painter.restore()


# Parsing ---------------------------------------------------------------------------


def _tokens(source: str) -> list[str]:
    tokens, i = [], 0
    while i < len(source):
        c = source[i]
        if c == "\\":
            j = i + 1
            if j < len(source) and not source[j].isalpha():
                tokens.append("\\" + source[j])
                i = j + 1
                continue
            while j < len(source) and source[j].isalpha():
                j += 1
            tokens.append(source[i:j])
            i = j
        elif c.isspace():
            i += 1
        else:
            tokens.append(c)
            i += 1
    return tokens


class _Parser:
    def __init__(self, source: str):
        self.tokens = _tokens(source)
        self.position = 0

    def peek(self):
        return self.tokens[self.position] if self.position < len(self.tokens) else None

    def take(self):
        token = self.peek()
        if token is None:
            raise MathError("The equation ends too early.")
        self.position += 1
        return token

    def row(self, until: set[str] = frozenset()) -> list:
        nodes = []
        while self.peek() is not None and self.peek() not in until:
            token = self.peek()
            if token in ("^", "_"):
                self.take()
                argument = self.argument()
                base = nodes.pop() if nodes else ("char", "")
                if base[0] != "scripts":
                    base = ("scripts", base, None, None)
                _, inner, sup, sub = base
                if token == "^":
                    if sup is not None:
                        raise MathError("Two superscripts in a row: put one in braces.")
                    sup = argument
                else:
                    if sub is not None:
                        raise MathError("Two subscripts in a row: put one in braces.")
                    sub = argument
                nodes.append(("scripts", inner, sup, sub))
            elif token == "'":
                self.take()
                nodes.append(("char", "′"))
            else:
                nodes.append(self.atom())
        return nodes

    def argument(self):
        """One atom or a {group}, as a row."""
        if self.peek() == "{":
            self.take()
            nodes = self.row({"}"})
            if self.peek() != "}":
                raise MathError("A { has no matching }.")
            self.take()
            return nodes
        return [self.atom()]

    def text_argument(self) -> str:
        if self.peek() != "{":
            return self.take().lstrip("\\")
        self.take()
        depth, parts = 0, []
        while True:
            token = self.take()
            if token == "{":
                depth += 1
            elif token == "}":
                if depth == 0:
                    break
                depth -= 1
            parts.append(token if not token.startswith("\\") or len(token) == 2 else token + " ")
        return "".join(p[1:] if p in ("\\{", "\\}") else p for p in parts)

    def atom(self):
        token = self.take()
        if token == "{":
            nodes = self.row({"}"})
            if self.peek() != "}":
                raise MathError("A { has no matching }.")
            self.take()
            return ("group", nodes)
        if token == "}":
            raise MathError("A } has no matching {.")
        if not token.startswith("\\"):
            if token in BINARY:
                return ("binary", BINARY[token])
            if token in RELATIONS:
                return ("relation", RELATIONS[token])
            return ("char", token)
        name = token[1:]
        if name == "frac" or name == "dfrac" or name == "tfrac":
            return ("frac", self.argument(), self.argument())
        if name == "sqrt":
            index = None
            if self.peek() == "[":
                self.take()
                index = self.row({"]"})
                if self.peek() != "]":
                    raise MathError("\\sqrt[ needs a closing ].")
                self.take()
            return ("sqrt", self.argument(), index)
        if name in ("text", "mathrm", "textrm", "operatorname", "mathbf", "textbf"):
            return ("text", self.text_argument(), name in ("mathbf", "textbf"))
        if name == "left":
            opening = self.take()
            inner = self.row({"\\right"})
            if self.peek() != "\\right":
                raise MathError("\\left needs a \\right.")
            self.take()
            closing = self.take()
            return ("delimited", _delimiter(opening), inner, _delimiter(closing))
        if name in ("right",):
            raise MathError("\\right without \\left.")
        if name in ACCENTS:
            return ("accent", name, self.argument())
        if name in SPACES:
            return ("space", SPACES[name])
        if name in GREEK:
            return ("char", GREEK[name])
        if name in RELATIONS:
            return ("relation", RELATIONS[name])
        if name in BINARY:
            return ("binary", BINARY[name])
        if name in BIG_OPERATORS:
            return ("bigop", name)
        if name in FUNCTIONS:
            return ("function", name)
        if name in SYMBOLS:
            return ("char", SYMBOLS[name])
        if len(name) == 1:
            return ("char", name)
        raise MathError(f"\\{name} is not something this editor knows.")


def _delimiter(token: str) -> str:
    return {".": "", "\\{": "{", "\\}": "}", "\\|": "‖", "\\langle": "⟨", "\\rangle": "⟩", "\\lbrace": "{",
            "\\rbrace": "}", "\\lvert": "|", "\\rvert": "|"}.get(token, token)


# Layout -----------------------------------------------------------------------------


class _Layout:
    def __init__(self, size: float):
        self.size = size

    def font(self, size: float, family: str = MATH_FONT, italic: bool = False, bold: bool = False) -> QFont:
        font = QFont(family)
        font.setPixelSize(max(1, round(size * OVERSIZE)))
        font.setItalic(italic)
        font.setBold(bold)
        return font

    def glyph(self, text: str, size: float, family=MATH_FONT, italic=False, bold=False) -> Glyph:
        font = self.font(size, family, italic, bold)
        metrics = QFontMetricsF(font)
        # The font's ascent and descent leave room for accents; real glyphs sit a little inside them.
        return Glyph(width=metrics.horizontalAdvance(text) / OVERSIZE, ascent=metrics.ascent() / OVERSIZE * 0.78,
                     descent=metrics.descent() / OVERSIZE * 0.6, text=text, font=font)

    def row(self, nodes, size: float, depth: int = 0) -> Row:
        items, x, ascent, descent = [], 0.0, size * 0.7, size * 0.2
        previous = None
        for node in nodes:
            kind = node[0]
            spacing = (0.22 if kind == "binary" else 0.28 if kind == "relation" else 0.0) * size
            if depth > 0:
                spacing *= 0.5
            if kind == "binary" and previous in (None, "binary", "relation"):
                spacing = 0.0  # a sign, as in -x
            before = spacing
            if previous == "function" and kind not in ("binary", "relation", "delimited") and not (
                    kind == "char" and node[1] in "([|"):
                before += 0.17 * size  # sin x, not sinx
            box = self.node(node, size, depth)
            x += before
            items.append((x, 0.0, box))
            x += box.width + spacing
            ascent, descent = max(ascent, box.ascent), max(descent, box.descent)
            is_function = kind == "function" or (kind == "scripts" and node[1][0] == "function")
            previous = "function" if is_function else kind
        return Row(width=x, ascent=ascent, descent=descent, items=[(dx, 0.0, box) for dx, _, box in items])

    def node(self, node, size: float, depth: int) -> Box:
        kind = node[0]
        if kind == "char":
            text = node[1]
            italic = text.isalpha() and text.isascii() or text in GREEK.values() and text.islower()
            family = TEXT_FONT if text.isascii() and text.isalnum() else MATH_FONT
            return self.glyph(text, size, family, italic=italic)
        if kind in ("binary", "relation"):
            return self.glyph(node[1], size)
        if kind == "group":
            return self.row(node[1], size, depth)
        if kind == "space":
            return Box(width=node[1] * size)
        if kind == "text":
            return self.glyph(node[1], size, TEXT_FONT, bold=node[2])
        if kind == "function":
            return self.glyph(node[1], size, TEXT_FONT)
        if kind == "scripts":
            return self.scripts(node[1], node[2], node[3], size, depth)
        if kind == "frac":
            return self.fraction(node[1], node[2], size, depth)
        if kind == "sqrt":
            return self.root(node[1], node[2], size, depth)
        if kind == "bigop":
            return self.glyph(BIG_OPERATORS[node[1]], size * (1.6 if node[1] in INTEGRALS else 1.4))
        if kind == "delimited":
            return self.delimited(node[1], node[2], node[3], size, depth)
        if kind == "accent":
            return self.accent(node[1], node[2], size, depth)
        raise MathError(f"Unknown part: {kind}")

    def scripts(self, base, sup, sub, size, depth) -> Box:
        limits = base[0] == "bigop" and base[1] not in INTEGRALS or base[0] == "function" and base[1] in LIMIT_FUNCTIONS
        body = self.node(base, size, depth)
        small = size * (SCRIPT if depth < 2 else 0.85)
        top = self.row(sup, small, depth + 1) if sup is not None else None
        bottom = self.row(sub, small, depth + 1) if sub is not None else None
        if limits:  # above and below, centred, as for a sum
            width = max(body.width, top.width if top else 0, bottom.width if bottom else 0)
            items = [((width - body.width) / 2, 0.0, body)]
            ascent, descent = body.ascent, body.descent
            if top:
                up = body.ascent + size * 0.12 + top.descent
                items.append(((width - top.width) / 2, up, top))
                ascent = up + top.ascent
            if bottom:
                down = body.descent + size * 0.1 + bottom.ascent
                items.append(((width - bottom.width) / 2, -down, bottom))
                descent = down + bottom.descent
            return Row(width=width, ascent=ascent, descent=descent, items=items)
        items = [(0.0, 0.0, body)]
        x = body.width + size * 0.03
        ascent, descent = body.ascent, body.descent
        width = body.width
        if top:
            up = max(size * 0.38, body.ascent - top.ascent * 0.45)
            items.append((x, up, top))
            ascent = max(ascent, up + top.ascent)
            width = max(width, x + top.width)
        if bottom:
            down = max(size * 0.18, bottom.ascent * 0.6) if not top else max(size * 0.25, bottom.ascent * 0.7)
            italic_correction = -size * 0.08 if base[0] == "bigop" else 0.0
            items.append((x + italic_correction, -down, bottom))
            descent = max(descent, down + bottom.descent)
            width = max(width, x + italic_correction + bottom.width)
        return Row(width=width + size * 0.04, ascent=ascent, descent=descent, items=items)

    def fraction(self, top_nodes, bottom_nodes, size, depth) -> Box:
        part = size * (0.9 if depth == 0 else SCRIPT)
        top = self.row(top_nodes, part, depth + 1)
        bottom = self.row(bottom_nodes, part, depth + 1)
        width = max(top.width, bottom.width) + size * 0.2
        bar = max(size * 0.05, 0.4)
        axis = size * AXIS
        gap = size * 0.12
        up = axis + bar / 2 + gap + top.descent
        down = -(axis - bar / 2 - gap - bottom.ascent)
        items = [((width - top.width) / 2, up, top), ((width - bottom.width) / 2, -down, bottom),
                 (size * 0.05, axis - bar / 2, Rule(width=width - size * 0.1, ascent=bar, descent=0.0))]
        return Row(width=width + size * 0.05, ascent=up + top.ascent, descent=down + bottom.descent, items=items)

    def root(self, inner_nodes, index_nodes, size, depth) -> Box:
        inner = self.row(inner_nodes, size, depth)
        gap = size * 0.55
        radical = Radical(width=gap + inner.width + size * 0.08, ascent=inner.ascent + size * 0.18,
                          descent=inner.descent + size * 0.04, size=size, inner=inner, gap=gap)
        if not index_nodes:
            return radical
        index = self.row(index_nodes, size * 0.5, depth + 2)
        return Row(width=radical.width, ascent=max(radical.ascent, inner.ascent * 0.6 + index.ascent + index.descent),
                   descent=radical.descent, items=[(0.0, 0.0, radical), (gap * 0.1, inner.ascent * 0.55 + index.descent, index)])

    def delimited(self, opening, inner_nodes, closing, size, depth) -> Box:
        inner = self.row(inner_nodes, size, depth)
        ascent, descent = max(inner.ascent, size * 0.75) + size * 0.06, max(inner.descent, size * 0.25) + size * 0.06
        items, x = [], 0.0
        for text in (opening, None, closing):
            if text is None:
                items.append((x, 0.0, inner))
                x += inner.width
                continue
            if not text:
                continue
            natural = self.glyph(text, size)
            tall = Stretched(width=natural.width * (1 + 0.1 * max(0, (ascent + descent) / size - 1)), ascent=ascent,
                             descent=descent, text=text, font=natural.font, natural=(natural.ascent, natural.descent))
            items.append((x, 0.0, tall))
            x += tall.width
        return Row(width=x, ascent=ascent, descent=descent, items=items)

    def accent(self, name, nodes, size, depth) -> Box:
        body = self.row(nodes, size, depth)
        if ACCENTS[name] is None:  # \overline
            rule = Rule(width=body.width, ascent=max(size * 0.05, 0.4), descent=0.0)
            up = body.ascent + size * 0.08
            return Row(width=body.width, ascent=up + rule.ascent, descent=body.descent,
                       items=[(0.0, 0.0, body), (0.0, up, rule)])
        mark = self.glyph(ACCENTS[name], size * (0.6 if name == "vec" else 0.9))
        up = body.ascent - mark.ascent * (0.15 if name == "vec" else 0.45)
        return Row(width=max(body.width, mark.width), ascent=up + mark.ascent, descent=body.descent,
                   items=[(0.0, 0.0, body), ((body.width - mark.width) / 2 + size * 0.05, up, mark)])


def layout(source: str, size: float) -> Box:
    """The equation as a box, in points at font size `size`. Raises MathError on mistakes."""
    parser = _Parser(source)
    nodes = parser.row()
    if parser.peek() is not None:
        raise MathError(f"Unexpected {parser.peek()}.")
    if not nodes:
        raise MathError("The equation is empty.")
    return _Layout(size).row(nodes, size)


def render(source: str, size: float, color=(0.0, 0.0, 0.0), scale: float = 4.0) -> QImage:
    """The equation as a transparent picture, `scale` pixels per point."""
    box = layout(source, size)
    margin = size * 0.15
    width = box.width + 2 * margin
    height = box.ascent + box.descent + 2 * margin
    image = QImage(max(1, round(width * scale)), max(1, round(height * scale)), QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
    painter.scale(scale, scale)
    ink = QColor.fromRgbF(*color)
    painter.setPen(ink)
    painter.setBrush(ink)
    box.draw(painter, margin, margin + box.ascent)
    painter.end()
    return image
