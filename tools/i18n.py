"""Translations of Zenith PDF: the texts to translate, and the calls that show them.

    python tools/i18n.py extract     the English texts and templates, into locales/catalog.json, and for
                                     each language the ones it still lacks (locales/missing-<code>.json)
    python tools/i18n.py wrap FILE   pass the text of the Qt calls that show it through tr(), in FILE

A text shown with QLabel, a button, setText, setToolTip, a window title, a menu entry, a list item or
drawText goes through core.i18n.tr when it is shown, so the source stays English. extract finds what to
translate: every string and f-string of the app that reads as words (f-strings as templates, "Page {}").
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "zenith_pdf"
LOCALES = PACKAGE / "locales"

# Calls that show the text in these positional arguments.
FIRST = {"QLabel", "QPushButton", "QCheckBox", "QRadioButton", "QGroupBox", "QListWidgetItem", "setText", "setToolTip",
         "setWindowTitle", "setPlaceholderText", "setTitle", "setStatusTip", "show_message", "setHtml",
         "setMarkdown", "showMessage"}
SECOND = {"setTabText", "setItemText"}
FLEXIBLE = {"addAction", "addMenu", "addItem", "QAction", "insertItem"}  # (text, ...) or (icon, text, ...)
LAST = {"drawText"}
TEXT_NAMES = {"text", "label", "title", "name", "tip", "tooltip", "caption", "heading", "message", "detail",
              "summary", "description", "what", "how", "keys", "hint", "note", "subtitle", "verb", "prompt", "intro",
              "line", "words", "status", "action", "reason", "problem", "kind_name", "html", "body"}


def _final_name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Call):
        return _final_name(node.func)
    return ""


def text_like(node) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and any(ch.isalpha() for ch in node.value)
    if isinstance(node, ast.JoinedStr):
        return True
    if isinstance(node, ast.BinOp):
        return text_like(node.left) or text_like(node.right)
    if isinstance(node, ast.IfExp):
        return text_like(node.body) or text_like(node.orelse)
    if isinstance(node, ast.Call):
        name = _final_name(node.func)
        if name in ("native_keys", "format", "join", "strip", "replace", "upper", "lower", "capitalize"):
            return True
        if name == "tr":
            return False
    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript)):
        base = node.value if isinstance(node, ast.Subscript) else node
        name = _final_name(base).lower().lstrip("_")
        return name in TEXT_NAMES or any(name.endswith("_" + t) for t in TEXT_NAMES)
    return False


def _targets(call: ast.Call) -> list:
    name = _final_name(call.func)
    args = call.args
    if not args or any(isinstance(a, ast.Starred) for a in args):
        return []
    if name in FIRST:
        return [args[0]] if text_like(args[0]) else []
    if name in SECOND:
        return [args[1]] if len(args) > 1 and text_like(args[1]) else []
    if name in LAST:
        return [args[-1]] if len(args) >= 2 and text_like(args[-1]) else []
    if name in FLEXIBLE:
        if text_like(args[0]):
            return [args[0]]
        if len(args) > 1 and text_like(args[1]):
            return [args[1]]
    return []


def wrap(path: Path) -> int:
    source = path.read_text(encoding="utf-8")
    data = source.encode("utf-8")
    starts = [0]
    for line in data.splitlines(keepends=True):
        starts.append(starts[-1] + len(line))
    tree = ast.parse(source)
    edits = []  # (start, end, replacement bytes)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for arg in _targets(node):
            inner = arg
            # native_keys(x) shows x with the keys a Mac writes: translate x first, then the keys.
            if isinstance(arg, ast.Call) and _final_name(arg.func) == "native_keys" and arg.args:
                inner = arg.args[0]
                if isinstance(inner, ast.Call) and _final_name(inner.func) == "tr":
                    continue
            start = starts[inner.lineno - 1] + inner.col_offset
            end = starts[inner.end_lineno - 1] + inner.end_col_offset
            edits.append((start, end))
    if not edits:
        return 0
    edits = sorted(set(edits), reverse=True)
    for start, end in edits:
        data = data[:start] + b"tr(" + data[start:end] + b")" + data[end:]
    text = data.decode("utf-8")
    depth = len(path.relative_to(PACKAGE).parts) - 1
    module = "i18n" if path.parent.name == "core" else "core.i18n"
    dots = "." * (depth if path.parent.name == "core" else depth + 1)
    statement = f"from {dots}{module} import tr\n"
    if statement not in text:
        lines = text.splitlines(keepends=True)
        at = next((i for i, line in enumerate(lines) if line.startswith("from .")), None)
        if at is None:
            at = max(i for i, line in enumerate(lines) if line.startswith(("import ", "from "))) + 1
        lines.insert(at, statement)
        text = "".join(lines)
    path.write_text(text, encoding="utf-8")
    return len(edits)


# Extracting ----------------------------------------------------------------------------------------

# Files whose strings never reach the screen: the assistant's vocabulary, PDF drawing commands, LaTeX, fonts, SQL,
# the self-test's report, Obsidian's file format.
SKIP_FILES = {"i18n.py", "lexicon.py", "appearance.py", "mathtext.py", "platforms.py", "library_index.py",
              "obsidian.py", "app.py"}
OPERATOR = re.compile(r"^([\d.{}/+-]+|[A-Za-z*'\"]{1,2}|/\w+)$")
# A stylesheet ends with "{", "}" or ";"; a text ending with a {} it is filled in with does not.
NOT_TEXT = re.compile(r"^[a-z0-9_.:/#\\-]+$|^[A-Z][A-Za-z]+(#|\[|::|\s*\{)|(?<!\{)[{};]\s*$|^#[0-9a-fA-F]{3,8}$|"
                      r"^(Q[A-Z]|rgba?\(|url\(|http|ph\.|%[a-z])")


def _templates(node: ast.JoinedStr) -> list[str]:
    """The f-string as templates, "Page {}": one for each choice of a part that picks between plain words,
    so f"{n} page{'s' if n != 1 else ''}" gives "{} pages" and "{} page"."""
    variants = [""]
    for value in node.values:
        if isinstance(value, ast.Constant):
            piece = [str(value.value).replace("{", "{{").replace("}", "}}")]
        else:
            expression = value.value
            if (isinstance(expression, ast.IfExp) and isinstance(expression.body, ast.Constant)
                    and isinstance(expression.orelse, ast.Constant)):
                piece = [str(expression.body.value), str(expression.orelse.value)]
            else:
                piece = ["{}"]
        variants = [v + p for v in variants for p in piece]
    return variants


def _docstrings(tree) -> set[int]:
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                found.add(id(first.value))
    return found


def _ignored(tree) -> set[int]:
    """Strings that never reach the user: log lines, dictionary keys of settings, raise of programming errors."""
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and _final_name(node.func) in {"debug", "info", "warning", "error",
                                                                     "exception", "getLogger", "setObjectName",
                                                                     "setProperty", "property", "get", "set",
                                                                     "setStyleSheet", "icon", "cached_icon",
                                                                     "icon_pixmap", "connect", "startswith",
                                                                     "endswith", "compile", "sub", "match", "search",
                                                                     "findall", "split", "setValue", "value"}:
            for sub in ast.walk(node):
                if isinstance(sub, (ast.Constant, ast.JoinedStr)) and sub is not node:
                    found.add(id(sub))
    return found


def reads_as_words(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < 2 or not any(ch.isalpha() for ch in stripped) or NOT_TEXT.search(stripped):
        return False
    if "\\" in stripped or "(?" in stripped:
        return False  # a regular expression
    tokens = stripped.split()
    if all(OPERATOR.match(token) for token in tokens):
        return False  # a PDF drawing command: "0 {} m {} {} l S"
    if len(tokens) == 1 and (stripped.isupper() or any(ch.isupper() for ch in stripped[1:])):
        return False  # JPEG, CMYK, KB; CamelCase names of objects and styles
    if re.search(r"%[A-Za-z]", stripped):
        return False  # a date format
    plain = not any(ch.isupper() for ch in stripped) and not any(ch in stripped for ch in ".,:;!?…(){}'’")
    if plain and len(tokens) >= 2:
        return False  # search keywords, icon names, stop words: lowercase lists, never shown
    if " " in stripped:
        return True
    return stripped[0].isupper() and stripped[1:].replace("…", "").replace("&", "").isalpha()


def extract() -> dict[str, list[str]]:
    """English text -> where it is (file:line), for every text of the app."""
    found: dict[str, list[str]] = {}
    for path in sorted(PACKAGE.rglob("*.py")):
        if path.name in SKIP_FILES:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        skip = _docstrings(tree) | _ignored(tree)
        inside_fstring = {id(v) for n in ast.walk(tree) if isinstance(n, ast.JoinedStr) for v in n.values}
        for node in ast.walk(tree):
            if id(node) in skip:
                continue
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in inside_fstring:
                text = node.value
            elif isinstance(node, ast.JoinedStr):
                text = None
            else:
                continue
            where = f"{path.relative_to(PACKAGE).as_posix()}:{node.lineno}"
            for variant in ([text] if text is not None else _templates(node)):
                if reads_as_words(variant):
                    found.setdefault(variant, []).append(where)
    return found


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "wrap":
        for name in sys.argv[2:]:
            print(name, wrap(Path(name).resolve()))
        return 0
    if len(sys.argv) >= 2 and sys.argv[1] == "extract":
        found = extract()
        LOCALES.mkdir(exist_ok=True)
        (LOCALES / "catalog.json").write_text(json.dumps({k: v[0] for k, v in sorted(found.items())}, indent=1,
                                                         ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"{len(found)} texts")
        for path in sorted(LOCALES.glob("??.json")):
            table = json.loads(path.read_text(encoding="utf-8"))
            missing = {k: "" for k in found if k not in table}
            print(f"{path.stem}: {len(table)} translated, {len(missing)} missing")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
