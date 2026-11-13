"""Bake the shaders of the Liquid glass's lens with Qt's shader baker.

    python tools/shaders.py            bake lens.vert and lens.frag into lens.vert.qsb and lens.frag.qsb
    python tools/shaders.py --check    exit 1 when a source is no longer the one its pack was baked from

The sources and their packs are in src/zenith_pdf/assets. The app loads the packs and never the sources, so
bake again after changing a shader. A pack holds its shader as SPIR-V, HLSL (Direct3D), MSL (Metal) and GLSL
(OpenGL), which Qt picks from; the lens draws through Direct3D. Baking also writes lens.baked, the SHA-256 of
each source the packs were baked from: --check and the tests compare the sources with it, without the baker.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

ASSETS = Path(__file__).resolve().parents[1] / "src" / "zenith_pdf" / "assets"
BAKED = ASSETS / "lens.baked"
QSB = Path(sys.executable).with_name("pyside6-qsb.exe" if os.name == "nt" else "pyside6-qsb")
TARGETS = ("--glsl", "330,300 es,120,100 es", "--hlsl", "50", "--msl", "12")
SOURCES = ("lens.vert", "lens.frag")


def digest(source: Path) -> str:
    """The SHA-256 of a shader's text, the same however a checkout ends its lines."""
    return hashlib.sha256(source.read_text(encoding="utf-8").encode("utf-8")).hexdigest()


def bake(source: Path, target: Path) -> None:
    if not QSB.is_file():
        raise SystemExit(f"{QSB.name} was not found beside {Path(sys.executable).name}: it comes with PySide6.")
    # Without a console the baker reports a wrong option in a dialog box and waits there: keep it on stderr.
    environment = {**os.environ, "QT_COMMAND_LINE_PARSER_NO_GUI_MESSAGE_BOXES": "1", "QT_FORCE_STDERR_LOGGING": "1"}
    done = subprocess.run([str(QSB), *TARGETS, "-o", str(target), str(source)], env=environment, timeout=120,
                          capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if done.returncode != 0 or done.stderr.strip() or not target.is_file():
        raise SystemExit(f"{source.name} did not bake ({done.returncode}):\n{done.stdout}{done.stderr}")


def stale() -> list[str]:
    """The sources whose pack is missing, or was baked from another text than theirs now."""
    lines = BAKED.read_text(encoding="utf-8").splitlines() if BAKED.is_file() else []
    baked = dict(reversed(line.split()) for line in lines)
    return [name for name in SOURCES
            if baked.get(name) != digest(ASSETS / name) or not (ASSETS / f"{name}.qsb").is_file()]


def main() -> int:
    if sys.argv[1:] == ["--check"]:
        changed = stale()
        for name in changed:
            print(f"{name}.qsb is not baked from {name} as it is now: python tools/shaders.py")
        return 1 if changed else 0
    if sys.argv[1:]:
        print(__doc__)
        return 1
    digests = {name: digest(ASSETS / name) for name in SOURCES}
    for name in SOURCES:
        target = ASSETS / f"{name}.qsb"
        bake(ASSETS / name, target)
        print(f"{target.name}: {target.stat().st_size} bytes")
    # Written last: a bake that fails halfway leaves the list as it was, and --check says so.
    BAKED.write_text("".join(f"{digests[name]}  {name}\n" for name in SOURCES), encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
