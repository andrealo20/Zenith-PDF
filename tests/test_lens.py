"""The lens of the Liquid glass: what its shader draws, against the same bend worked out here on the CPU, and
what the module does when a frame fails, when it is asked before it is started and when the program ends.

The lens draws through Direct3D, so these run on Windows alone. There they run the real shader: on the graphics
card, or on a PC without one on Windows' software device, whose pictures differ by a level here and there. So
pictures are compared with a tolerance, never byte for byte."""

from __future__ import annotations

import hashlib
import logging
import math
import os
import subprocess
import sys
import textwrap
from dataclasses import replace
from pathlib import Path

import pytest
from PySide6.QtCore import QSize
from PySide6.QtGui import QColor, QImage, QPainter, QShader

from zenith_pdf.ui import lens

pytestmark = pytest.mark.skipif(not lens.supported(), reason="the lens draws through Direct3D: Windows alone")

BAR = (300, 60)  # logical px
BLEED = 24  # logical px of page the pictures cover beyond the bar on every side, as a floating bar samples it
INDEX = 1.5  # the glass's
SLOPE_CAP = 8.0  # where the rim's profile turns vertical
# The look these tests were worked out for, whatever the app's own becomes: under glass this thick the bend is
# at its most over the outer half of the rim, then falls to nothing where the rim ends.
LOOK = {"band": 9.0, "thick": 54.0, "most": 8.0, "fringe": 0.03, "ease": 0.8, "profile": "circle"}
STEEP = 8  # levels per device px of the ramps: a bend off by a quarter of a pixel shows as 2 levels
FIRST = 20  # the ramps' level at the bar's edge
RED = 40  # in every pixel of the ramps and of the middle: a pixel the lens left undrawn would have none
MIDDLE = (RED, 60, 0)  # the middle, where its picture does not matter
LEVELS = 2  # how far the lens may be from the model, in levels of 0 to 255


@pytest.fixture
def lens_on(qt_app):
    """The lens is up in this test: started once for the run (on Windows the software device is always there),
    and started again after a test that broke it, for the tests and the files that come after."""
    assert lens.start()
    yield
    lens._failed = False
    assert lens.start()


# The bend, worked out on the CPU ---------------------------------------------------------


def slope(inward: float, band: float, profile: str) -> float:
    """The slope of the glass's surface `inward` px from the edge of a rim `band` px wide (0 past the rim).
    "squircle": the height is (1 - u^4)^(1/4); "circle": a quarter circle, sqrt(1 - u^2); u = 1 - inward / band."""
    x = min(max(inward / band, 0.0), 1.0)
    if x >= 1.0:
        return 0.0
    u = 1.0 - x
    if profile == "circle":
        value = u / math.sqrt(max(1.0 - u * u, 1e-6))
    else:
        value = u ** 3 * max(1.0 - u ** 4, 1e-6) ** -0.75
    return min(value, SLOPE_CAP)


def bend(inward: float, band: float, thick: float, most: float, profile: str) -> float:
    """How far towards the inside (px, as the arguments) the page is read `inward` px from a straight edge: a
    ray straight down, (0, 0, -1), refracted by Snell's law (INDEX) through the surface whose normal is
    normalize(n * slope, 1), followed through `thick` px of glass, and never further than `most`."""
    tilt = slope(inward, band, profile)
    if tilt <= 0.0:
        return 0.0
    cos_in = 1.0 / math.sqrt(1.0 + tilt * tilt)
    eta = 1.0 / INDEX
    k = 1.0 - eta * eta * (1.0 - cos_in * cos_in)
    factor = eta * cos_in - math.sqrt(k)  # GLSL's refract(): T = eta * I + factor * N, with dot(N, I) = -cos_in
    along_normal = factor * tilt * cos_in  # T's part along the outward normal: negative, so towards the inside
    down = -eta + factor * cos_in  # T.z, negative
    return min(-along_normal / max(-down, 1e-3) * thick, most)


def smoothstep(low: float, high: float, value: float) -> float:
    t = min(max((value - low) / (high - low), 0.0), 1.0)
    return t * t * (3 - 2 * t)


def read(image: QImage, x: float, y: float) -> tuple[float, float, float]:
    """`image` at (x, y) in its own pixels, as a linear texture filter reads it (edges repeated)."""
    x, y = x - 0.5, y - 0.5
    left, top = math.floor(x), math.floor(y)
    fx, fy = x - left, y - top
    total = [0.0, 0.0, 0.0]
    for dx, dy, weight in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        pixel = image.pixel(min(max(left + dx, 0), image.width() - 1), min(max(top + dy, 0), image.height() - 1))
        for channel, shift in enumerate((16, 8, 0)):
            total[channel] += weight * (pixel >> shift & 0xFF)
    return total[0], total[1], total[2]


def rgb(image: QImage, x: int, y: int) -> tuple[int, int, int]:
    pixel = image.pixel(x, y)
    return pixel >> 16 & 0xFF, pixel >> 8 & 0xFF, pixel & 0xFF


def shown(sharp: QImage, spec: lens.Spec, x: int, y: int, depth: float, inwards: tuple[float, float]) -> float:
    """The green the lens must show at pixel (x, y), `depth` px inside the bar's outline, where `inwards` is the
    way into the bar: the page from bend() further in, giving way to the MIDDLE over the inner part of the rim."""
    ratio = spec.ratio
    band = spec.band * ratio
    by = bend(depth, band, spec.thick * ratio, spec.most * ratio, spec.profile)
    green = read(sharp, x + 0.5 - spec.place[0] + inwards[0] * by, y + 0.5 - spec.place[1] + inwards[1] * by)[1]
    share = 0.0 if spec.ease >= 1.0 else smoothstep(spec.ease, 1.0, min(max(depth / band, 0.0), 1.0))
    return green * (1 - share) + MIDDLE[1] * share


def depth_inside(x: float, y: float, spec: lens.Spec) -> float:
    """How far inside the outline of a bar with round corners the point (x, y) is, in px."""
    half_w, half_h = spec.width / 2, spec.height / 2
    corner = min(spec.radius, half_w, half_h)
    ex, ey = abs(x - half_w) - half_w + corner, abs(y - half_h) - half_h + corner
    return corner - math.hypot(max(ex, 0.0), max(ey, 0.0)) - min(max(ex, ey), 0.0)


# Pictures --------------------------------------------------------------------------------


def bar(ratio: float, **more) -> lens.Spec:
    """A BAR at `ratio`, a capsule unless `more` says otherwise, over pictures that cover it and the bleed
    round it. Drawn with the bend alone: no veil, no light, no edge, no shadow, the page's colours as they are."""
    width, height = round(BAR[0] * ratio), round(BAR[1] * ratio)
    area = (round((BAR[0] + 2 * BLEED) * ratio), round((BAR[1] + 2 * BLEED) * ratio))
    plain = {"radius": height / 2, "place": (-BLEED * ratio, -BLEED * ratio), "shade": 0.0, "saturation": 1.0}
    return lens.Spec(width, height, ratio, area=area, **{**LOOK, **plain, **more})


def ramp(ratio: float, edge: str) -> QImage:
    """What lies behind a BAR at `ratio`, with the bleed round it: green rising STEEP levels per device px into
    the bar from FIRST at its "top", "left" or "right" edge."""
    width, height = bar(ratio).area
    image = QImage(width, height, QImage.Format.Format_RGB32)
    painter = QPainter(image)
    for line in range(height if edge == "top" else width):
        depth = width - BLEED * ratio - line - 0.5 if edge == "right" else line + 0.5 - BLEED * ratio
        colour = QColor(RED, min(max(round(FIRST + STEEP * depth), 0), 255), 0)
        painter.fillRect(*((0, line, width, 1) if edge == "top" else (line, 0, 1, height)), colour)
    painter.end()
    return image


def flat(colour: tuple[int, int, int]) -> QImage:
    image = QImage(8, 8, QImage.Format.Format_RGB32)
    image.fill(QColor(*colour))
    return image


# What the lens draws ---------------------------------------------------------------------


def test_the_rim_bends_the_page_as_snell_says(lens_on):
    middle = flat(MIDDLE)
    for ratio in (1.0, 1.25, 1.5, 1.75):
        sharp = ramp(ratio, "top")
        for profile in ("circle", "squircle"):
            # A bar can start a fraction of a device pixel in: so do the pictures behind it here.
            spec = bar(ratio, profile=profile, place=(-BLEED * ratio - 0.25, -BLEED * ratio + 0.375))
            image = lens.render(sharp, middle, spec)
            assert image is not None and image.format() == QImage.Format.Format_RGB32, (ratio, profile)
            assert image.size() == QSize(spec.width, spec.height), (ratio, profile)  # the bar's own device pixels
            x, bent = spec.width // 2, 0
            for y in range(math.ceil(spec.band * ratio) + 1):  # down the rim, and a row past it
                green = rgb(image, x, y)[1]
                assert abs(green - shown(sharp, spec, x, y, y + 0.5, (0.0, 1.0))) <= LEVELS, (ratio, profile, y)
                # Read where it lies, the page is far darker than what the rim shows: no model without a bend
                # would do.
                bent += green - read(sharp, x + 0.5 - spec.place[0], y + 0.5 - spec.place[1])[1] > 3 * LEVELS
            assert bent >= 3, (ratio, profile)
            # The picture ends where the bar does: nothing is left undrawn along its right and its bottom.
            assert all(abs(rgb(image, spec.width - 1, y)[0] - RED) <= 1 for y in range(spec.height)), (ratio, profile)
            assert all(abs(rgb(image, x, spec.height - 1)[0] - RED) <= 1 for x in range(spec.width)), (ratio, profile)


def test_past_the_rim_the_bar_shows_the_middle(lens_on):
    ratio = 1.25
    spec = bar(ratio, place=(-BLEED * ratio + 0.375, -BLEED * ratio - 0.25))
    # As the bar's blurred picture: a texel for every two logical px of the area, stored red first. Red rises
    # to the right in it and green falls downwards.
    middle = QImage(round(spec.area[0] / ratio / 2), round(spec.area[1] / ratio / 2), QImage.Format.Format_RGBX8888)
    painter = QPainter(middle)
    for x in range(middle.width()):
        painter.fillRect(x, 0, 1, middle.height(), QColor(40 + x, 0, 77))
    painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    for y in range(middle.height()):
        painter.fillRect(0, y, middle.width(), 1, QColor(0, 230 - 3 * y, 0))
    painter.end()
    image = lens.render(flat((200, 60, 30)), middle, spec)
    assert image is not None
    scale_x, scale_y = middle.width() / spec.area[0], middle.height() / spec.area[1]
    past = 0
    for y in range(spec.height):
        for x in range(spec.width):
            if depth_inside(x + 0.5, y + 0.5, spec) >= spec.band * ratio:
                wanted = read(middle, (x + 0.5 - spec.place[0]) * scale_x, (y + 0.5 - spec.place[1]) * scale_y)
                assert max(abs(a - b) for a, b in zip(rgb(image, x, y), wanted)) <= 1, (x, y)
                past += 1
    assert past > spec.width * spec.height / 2  # most of the bar


def test_a_rim_that_never_gives_way_shows_no_middle(lens_on):
    ratio = 1.25
    sharp = ramp(ratio, "top")
    # The rim as wide as half the bar: past it, at the very middle, the bend is nothing and the page shows as it is.
    spec = bar(ratio, ease=1.0, band=BAR[1] / 2)
    for middle in ((0, 0, 0), (255, 255, 255), (RED, 255, 200)):
        image = lens.render(sharp, flat(middle), spec)
        assert image is not None
        for y in range(spec.height):
            x = spec.width // 2
            assert abs(rgb(image, x, y)[1] - shown(sharp, spec, x, y, min(y, spec.height - 1 - y) + 0.5,
                                                   (0.0, 1.0 if y < spec.height / 2 else -1.0))) <= LEVELS, (middle, y)


def test_red_and_blue_part_where_the_bend_is_large(lens_on):
    ratio = 1.5
    spec = bar(ratio)
    band, thick, most = spec.band * ratio, spec.thick * ratio, spec.most * ratio
    # Down the outer rows of the rim the bend is at its most: each shows the page from `most` further in.
    outer = next(y for y in range(spec.height) if bend(y + 0.5, band, thick, most, spec.profile) < most)
    assert outer >= 6
    # A page white down to an edge that the middle of those rows shows, and black below it.
    edge = round(most) + outer // 2  # device px below the bar's top
    sharp = QImage(*spec.area, QImage.Format.Format_RGB32)
    sharp.fill(QColor(255, 255, 255))
    painter = QPainter(sharp)
    painter.fillRect(0, round(BLEED * ratio) + edge, sharp.width(), sharp.height(), QColor(0, 0, 0))
    painter.end()
    image = lens.render(sharp, flat((0, 0, 0)), spec)
    assert image is not None
    # How far down each colour stays white: a row half white counts as half a row.
    x = spec.width // 2
    red, green, blue = (sum(rgb(image, x, y)[channel] for y in range(outer)) / 255 for channel in range(3))
    # Red is read 1 - fringe of the bend further in and blue 1 + fringe: blue comes to the page's edge first,
    # nearest the bar's own, and red last.
    assert blue < green < red
    assert green == pytest.approx(edge - most, abs=0.05)
    assert red - green == pytest.approx(most * spec.fringe, abs=0.05)
    assert green - blue == pytest.approx(most * spec.fringe, abs=0.05)


def test_the_ends_of_a_capsule_and_a_cut_corner_bend_too(lens_on):
    ratio = 1.5
    middle = flat(MIDDLE)
    from_left, from_top, from_right = ramp(ratio, "left"), ramp(ratio, "top"), ramp(ratio, "right")
    capsule = bar(ratio)
    rim = range(math.floor(capsule.band * ratio * capsule.ease))  # the px of the rim before the middle shows in it

    def bends(image, sharp, spec, x, y, depth, inwards) -> bool:
        """Checks the pixel against the model; says whether the page read where it lies would be far from it."""
        green = rgb(image, x, y)[1]
        assert abs(green - shown(sharp, spec, x, y, depth, inwards)) <= LEVELS, (x, y)
        return green - read(sharp, x + 0.5 - spec.place[0], y + 0.5 - spec.place[1])[1] > 3 * LEVELS

    # The left end of a capsule, on its axis: the page from further right, by the whole bend.
    image = lens.render(from_left, middle, capsule)
    bent = 0
    for y in (capsule.height // 2 - 1, capsule.height // 2):
        for x in rim:
            dx, dy = x + 0.5 - capsule.radius, y + 0.5 - capsule.radius
            reach = math.hypot(dx, dy)
            bent += bends(image, from_left, capsule, x, y, capsule.radius - reach, (-dx / reach, -dy / reach))
    assert bent >= len(rim)

    # A corner cut at 45 degrees bends along the bar's diagonal: as far to the right as downwards.
    cut = 10 * ratio
    chamfered = bar(ratio, radius=cut, chamfer=True)
    half = math.sqrt(0.5)
    for sharp in (from_left, from_top):
        image = lens.render(sharp, middle, chamfered)
        bent = 0
        for x in range(math.ceil(cut / 2), math.ceil(cut / 2) + 6):
            depth = (2 * x + 1 - cut) * half  # from the cut, along the diagonal
            bent += bends(image, sharp, chamfered, x, x, depth, (half, half))
        assert bent >= 4

    # The square corner next to it keeps its two edges apart: beside the corner the top edge still bends
    # downwards and the right edge to the left, each by the whole bend.
    aside = math.ceil(chamfered.band * ratio) + 2  # further from the other edge than the rim is wide
    image = lens.render(from_top, middle, chamfered)
    x = chamfered.width - 1 - aside
    assert sum(bends(image, from_top, chamfered, x, y, y + 0.5, (0.0, 1.0)) for y in rim) >= len(rim) // 2
    image = lens.render(from_right, middle, chamfered)
    assert sum(bends(image, from_right, chamfered, chamfered.width - 1 - step, aside, step + 0.5, (-1.0, 0.0))
               for step in rim) >= len(rim) // 2


def test_the_light_comes_from_the_top_left(lens_on):
    ratio = 1.5
    page, behind, tint, veil, lift = (150, 100, 60), (70, 74, 80), (28, 29, 34), 0.17, 1.3
    # The dark theme's glass over a flat page: its tint thin over the rim, its light, its fine edge, a shadow.
    spec = bar(ratio, tint=tint, rim_veil=veil, saturation=lift, highlight=(255, 255, 255, 46),
               border=(255, 255, 255, 26), shade=0.2)
    image = lens.render(flat(page), flat(behind), spec)
    assert image is not None

    def brightness(columns, rows) -> float:
        lumas = [0.2126 * r + 0.7152 * g + 0.0722 * b for x in columns for y in rows for r, g, b in [rgb(image, x, y)]]
        return sum(lumas) / len(lumas)

    band = math.floor(spec.band * ratio)
    straight = range(spec.height, spec.width - spec.height)  # between the round ends
    axis = range(spec.height // 2 - 2, spec.height // 2 + 2)
    top, bottom = brightness(straight, range(band)), brightness(straight, range(spec.height - band, spec.height))
    left, right = brightness(range(band), axis), brightness(range(spec.width - band, spec.width), axis)
    assert top > bottom + 4 and left > right + 2, (top, bottom, left, right)

    # Down the top of the bar: the fine edge on whole device pixels (two at 150%), a line of light under it,
    # and a few px further in the rim alone, which is the page with its colours lifted under the veil of tint.
    x, edge = spec.width // 2, max(1, int(ratio + 0.5))
    luma = 0.2126 * page[0] + 0.7152 * page[1] + 0.0722 * page[2]
    veiled = [(luma + lift * (channel - luma)) * (1 - veil) + over * veil for channel, over in zip(page, tint)]
    rim = rgb(image, x, band // 2)
    assert all(abs(a - b) <= 1 for a, b in zip(rim, veiled)), rim
    assert all(abs(a - b) <= 1 for a, b in zip(rgb(image, x, 0), rgb(image, x, edge - 1)))
    assert sum(rgb(image, x, edge - 1)) > sum(rgb(image, x, edge)) + 25  # the edge, lighter than the line of light
    assert sum(rgb(image, x, edge)) > sum(rim) + 30  # and the line, lighter than the rim under it
    # Past the rim no light falls: the middle is the middle.
    assert all(abs(a - b) <= 1 for a, b in zip(rgb(image, x, spec.height // 2), behind))


# The device ------------------------------------------------------------------------------


def test_the_same_pictures_are_uploaded_once(lens_on):
    sharp, middle, spec = ramp(1.0, "top"), flat(MIDDLE), bar(1.0)
    before = lens._lens.uploads
    assert lens.render(sharp, middle, spec) is not None
    # The pages moved under the bar between two samples: the same pictures, 5 px further up.
    assert lens.render(sharp, middle, replace(spec, place=(spec.place[0], spec.place[1] - 5))) is not None
    assert lens._lens.uploads == before + 2
    assert lens.render(sharp.copy(), middle.copy(), spec) is not None  # a new sample: two new pictures
    assert lens._lens.uploads == before + 4


def test_a_bar_of_no_size_or_too_large_is_not_drawn(lens_on):
    sharp, middle, spec = ramp(1.0, "top"), flat(MIDDLE), bar(1.0)
    too_wide = lens._lens.limit + 1  # more than the device takes in one texture
    for odd in (replace(spec, width=0), replace(spec, height=0), replace(spec, width=too_wide)):
        assert lens.render(sharp, middle, odd) is None
    assert lens.render(QImage(), middle, spec) is None and lens.render(sharp, QImage(), spec) is None
    assert lens.render(sharp.scaled(too_wide, 2), middle, spec) is None  # a picture the device cannot take either
    assert lens.available()  # nothing to draw is no fault of the lens
    assert lens.render(sharp, middle, spec) is not None


def frame_does_not_start(patch) -> None:
    patch.setattr(lens._lens, "begin", lambda *_: 3)  # QRhi::FrameOpDeviceLost: a driver reset, a PC waking up


def call_fails_inside_the_frame(patch) -> None:
    def refused(*_):
        raise TypeError("a call the device refuses")

    patch.setattr(lens, "QRhiViewport", refused)  # between the start of the pass and its end


@pytest.mark.parametrize("broken", [frame_does_not_start, call_fails_inside_the_frame])
def test_a_failure_turns_the_lens_off_for_good_and_says_so_once(lens_on, monkeypatch, caplog, broken):
    sharp, middle, spec = ramp(1.0, "top"), flat(MIDDLE), bar(1.0)
    assert lens.render(sharp, middle, spec) is not None
    caplog.set_level(logging.WARNING, logger=lens.log.name)
    close, tried, left_open = lens._Lens.close, [], []

    def freed(self) -> None:
        left_open.append(self.rhi.isRecordingFrame())
        close(self)

    with monkeypatch.context() as patch:  # undone before lens_on starts the lens again
        broken(patch)
        patch.setattr(lens._Lens, "close", freed)
        assert lens.render(sharp, middle, spec) is None
        patch.setattr(lens, "_device", lambda: tried.append("a device"))  # what trying again would ask for
        assert not lens.available() and not lens.start()
        assert [lens.render(sharp, middle, spec) for _ in range(3)] == [None, None, None]
    assert not tried
    assert left_open == [False]  # the frame was closed before the device and all on it were freed
    said = [record for record in caplog.records if record.name == lens.log.name]
    assert len(said) == 1 and said[0].levelno == logging.WARNING and "Liquid glass" in said[0].getMessage()


def run(script: str) -> subprocess.CompletedProcess:
    """`script` in a process of its own, on no screen. What Qt has to say goes to its stderr, and so does the
    device's complaint about anything left on it when it goes."""
    environment = {**os.environ, "PYTHONPATH": str(Path(lens.__file__).parents[2]), "QT_QPA_PLATFORM": "offscreen",
                   "QT_FORCE_STDERR_LOGGING": "1", "QT_RHI_LEAK_CHECK": "1"}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(script)], env=environment, capture_output=True,
                          text=True, timeout=120, stdin=subprocess.DEVNULL)


DRAWS = """
    import sys
    from PySide6.QtCore import QTimer
    from PySide6.QtGui import QColor, QImage, QPainter
    from PySide6.QtWidgets import QApplication, QWidget
    from zenith_pdf.ui import lens

    app = QApplication(sys.argv)
    page = QImage(160, 96, QImage.Format.Format_RGB32)
    page.fill(QColor(90, 120, 150))
    spec = lens.Spec(width=120, height=48, radius=24.0, place=(-20.0, -24.0), area=(160.0, 96.0))
    drawn = []

    class Bar(QWidget):
        def paintEvent(self, event):
            image = lens.render(page, page, spec)
            painter = QPainter(self)
            if image is not None:
                painter.drawImage(0, 0, image)
                drawn.append(image)
            painter.end()

    assert lens.start()
    bar = Bar()
    bar.resize(120, 48)
    picture = bar.grab()  # paints the bar, on no screen
    assert len(drawn) == 1
"""
# As a script ends, with the application, the widget and the device alive; and as the app does, whose event
# loop quits first.
ENDS = ["", "QTimer.singleShot(0, app.quit)\napp.exec()\nassert not lens.available()\n"]


@pytest.mark.parametrize("end", ENDS, ids=["the script ends", "the application quits"])
def test_the_process_ends_cleanly_with_the_lens_in_use(end):
    done = run(textwrap.dedent(DRAWS) + end + 'print("drawn")\n')
    assert (done.returncode, done.stdout.strip(), done.stderr) == (0, "drawn", ""), done.stderr


def test_no_device_is_made_once_the_application_is_quitting():
    done = run("""
        import sys
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QApplication
        from zenith_pdf.ui import lens

        app = QApplication(sys.argv)
        made = []
        device = lens._device
        lens._device = lambda: made.append("a device") or device()
        assert lens.start() and made == ["a device"]
        asked = []
        app.aboutToQuit.connect(lambda: asked.append(lens.start()))  # a sample or a paint while the program closes
        QTimer.singleShot(0, app.quit)
        app.exec()
        assert asked == [False] and not lens.start() and not lens.available()
        assert made == ["a device"] and not lens._failed  # quitting is no failure of the lens
        print("made once")
    """)
    assert (done.returncode, done.stdout.strip(), done.stderr) == (0, "made once", ""), done.stderr


def test_nothing_is_made_at_import_or_by_asking():
    done = run("""
        import threading
        from PySide6.QtGui import QColor, QGuiApplication, QImage
        from zenith_pdf.ui import lens

        made = []
        device = lens._device
        lens._device = lambda: made.append("a device") or device()
        page = QImage(64, 64, QImage.Format.Format_RGB32)
        page.fill(QColor(90, 120, 150))
        spec = lens.Spec(width=40, height=20, radius=10.0, place=(-12.0, -22.0), area=(64.0, 64.0))

        def asked():
            return [lens.available(), lens.start(), lens.render(page, page, spec)]

        def elsewhere(work):  # on a thread that is not the application's
            found = []
            thread = threading.Thread(target=lambda: found.append(work()))
            thread.start()
            thread.join()
            return found[0]

        assert asked() == [False, False, None]  # before there is an application
        app = QGuiApplication([])
        assert elsewhere(asked) == [False, False, None]
        assert not lens.available() and lens.render(page, page, spec) is None  # asking is not starting
        assert not made and lens._lens is None and not lens._failed  # and none of it is held against the lens
        assert lens.start() and made == ["a device"] and lens.available()
        assert lens.render(page, page, spec) is not None
        # The device answers only to the thread that made it: no other gets a picture, or costs the lens anything.
        assert elsewhere(lambda: lens.render(page, page, spec)) is None
        assert lens.render(page, page, spec) is not None and made == ["a device"]
        print("made once")
    """)
    assert (done.returncode, done.stdout.strip(), done.stderr) == (0, "made once", ""), done.stderr


def test_a_pyside6_without_a_name_the_lens_needs_still_starts_the_app():
    done = run("""
        import PySide6.QtGui
        PySide6.QtGui.QRhiReadbackResult  # loaded first: PySide6 brings in its names when they are first asked for
        del PySide6.QtGui.QRhiReadbackResult  # as an older PySide6, which the app allows, may lack it
        import zenith_pdf.ui.widgets
        from zenith_pdf.ui import lens, widgets
        assert not lens.supported() and widgets.glass_style("liquid") == "clear"
        print("imported")
    """)
    assert (done.returncode, done.stdout.strip(), done.stderr) == (0, "imported", ""), done.stderr


def test_the_baked_shaders_are_those_of_their_sources():
    listed = (lens.ASSETS / "lens.baked").read_text(encoding="utf-8").splitlines()
    baked = dict(reversed(line.split()) for line in listed)
    for name in ("lens.vert", "lens.frag"):
        # Read as text: a checkout that ends its lines another way still has the source the pack was baked from.
        source = (lens.ASSETS / name).read_text(encoding="utf-8")
        assert baked[name] == hashlib.sha256(source.encode("utf-8")).hexdigest(), f"{name}: python tools/shaders.py"
        assert QShader.fromSerialized((lens.ASSETS / f"{name}.qsb").read_bytes()).isValid(), name
