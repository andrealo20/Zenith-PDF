"""Motion: the helpers jump to the end state with motion off and animate when it is on; the setting, the
environment and the system decide which. The suite runs with motion off (conftest); a test of an animation asks for
it with the motion_on fixture."""

import pytest
from PySide6.QtCore import QPoint, QRect, QRectF, QSettings, Qt
from PySide6.QtGui import QColor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFrame, QGraphicsOpacityEffect, QHBoxLayout, QWidget

from conftest import settle, wait_for
from zenith_pdf.settings import DEFAULTS, Settings
from zenith_pdf.styles import neon
from zenith_pdf.theme import DARK, LIGHT, bar_radius, qcolor, themes, tone_sheet
from zenith_pdf.ui import motion
from zenith_pdf.ui.widgets import FADE_MS, Fade, FloatingBar, IconButton, Segmented, Switch

BACKGROUND = QColor("#202020").rgb()
RED = QColor("#ff0000").rgb()

# What enabled() says ------------------------------------------------------------------------------------------


@pytest.fixture
def asking(monkeypatch, tmp_path):
    """enabled() asking its real questions: the environment, the setting (the Settings this yields) and a stand-in
    for the system that allows animations."""
    monkeypatch.delenv("ZENITH_NO_MOTION", raising=False)
    monkeypatch.setattr(motion, "system_animations", lambda: True)
    yield Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    motion.set_reduced(False)


def test_the_suite_runs_without_motion():
    assert not motion.enabled()


def test_the_fixture_turns_motion_on(motion_on):
    assert motion.enabled()


def test_enabled_follows_the_setting_the_environment_and_the_system(asking, monkeypatch):
    assert DEFAULTS["reduce_motion"] is False and motion.enabled()
    asking.set("reduce_motion", True)
    assert not motion.enabled()  # the moment it is set, with nothing restarted
    asking.set("reduce_motion", False)
    assert motion.enabled()
    asking.set("reduce_motion", True)
    asking.reset("reduce_motion")  # back to the default, which is off
    assert motion.enabled()
    monkeypatch.setenv("ZENITH_NO_MOTION", "1")
    assert not motion.enabled()
    monkeypatch.setenv("ZENITH_NO_MOTION", "0")  # 0 and empty read as not set
    assert motion.enabled()
    monkeypatch.setenv("ZENITH_NO_MOTION", "")
    assert motion.enabled()
    monkeypatch.delenv("ZENITH_NO_MOTION")
    monkeypatch.setattr(motion, "system_animations", lambda: False)  # Windows' "Animation effects" off
    assert not motion.enabled()


def test_a_settings_object_made_with_reduce_motion_on_starts_with_motion_off(asking):
    asking.set("reduce_motion", True)
    asking._store.sync()
    motion.set_reduced(False)
    Settings(QSettings(asking._store.fileName(), QSettings.Format.IniFormat))
    assert not motion.enabled()


def test_force_decides_over_everything_else(asking, monkeypatch):
    asking.set("reduce_motion", True)
    monkeypatch.setenv("ZENITH_NO_MOTION", "1")
    try:
        motion.force(True)
        assert motion.enabled()
        asking.set("reduce_motion", False)
        monkeypatch.delenv("ZENITH_NO_MOTION")
        motion.force(False)
        assert not motion.enabled()
    finally:
        motion.force(None)
    assert motion.enabled()


def test_the_system_answers_with_a_yes_or_no():
    assert isinstance(motion.system_animations(), bool)


# A number and a colour that ease --------------------------------------------------------------------------------


def test_a_smooth_number_jumps_with_motion_off(qt_app):
    smooth = motion.Smooth(value=2.0)
    seen = []
    smooth.changed.connect(seen.append)
    smooth.go(5.0, 1000)
    assert smooth.value == 5.0 and smooth.target == 5.0 and not smooth.running and seen == [5.0]
    smooth.go(5.0)
    assert seen == [5.0]  # already there: no news


def test_a_smooth_number_eases_and_arrives(qt_app, motion_on):
    smooth = motion.Smooth()
    smooth.go(10.0, 5000)
    assert smooth.running and smooth.target == 10.0 and smooth.value < 10.0
    smooth._animation.setCurrentTime(2500)  # half of its time, at an exact moment rather than by the clock
    assert 0.0 < smooth.value < 10.0  # on its way: three quarters of the way, the easing being out
    midway = smooth.value
    smooth.go(0.0, 60)  # turned back half way: from where it is
    assert smooth.target == 0.0 and smooth.value == midway
    assert wait_for(lambda: not smooth.running, 5) and smooth.value == 0.0
    smooth.go(10.0, 60)
    assert wait_for(lambda: not smooth.running, 5) and smooth.value == 10.0  # the end value, exactly
    smooth.go(0.0, 5000)
    smooth.set(3.0)  # at once, and nothing left running
    assert smooth.value == 3.0 and not smooth.running


def test_a_change_to_a_widget_not_shown_yet_lands_at_once(qt_app, motion_on):
    holder = QWidget()
    smooth = motion.Smooth(holder)
    smooth.go(1.0, 5000)
    assert smooth.value == 1.0 and not smooth.running  # nobody sees it move
    holder.show()
    smooth.go(0.0, 5000)
    assert smooth.running
    holder.close()
    holder.deleteLater()


def test_mixed_keeps_the_hue_of_a_colour_that_comes_from_clear():
    tint = QColor(255, 255, 255, 100)
    half = motion.mixed(QColor(0, 0, 0, 0), tint, 0.5)
    assert (half.red(), half.green(), half.blue()) == (255, 255, 255) and abs(half.alpha() - 50) <= 1
    assert motion.mixed(tint, QColor(0, 0, 0, 0), 0.5).rgba() == half.rgba()  # and goes the same way
    red, blue = QColor(255, 0, 0), QColor(0, 0, 255)
    assert motion.mixed(red, blue, 0.0) == red and motion.mixed(red, blue, 1.0) == blue
    middle = motion.mixed(red, blue, 0.5)
    assert abs(middle.red() - 128) <= 1 and abs(middle.blue() - 128) <= 1 and middle.alpha() == 255
    assert motion.mixed(red, blue, 7.0) == blue and motion.mixed(red, blue, -1.0) == red


def test_a_fill_jumps_with_motion_off(qt_app):
    fill = motion.Fill()
    seen = []
    fill.changed.connect(lambda: seen.append(1))
    tint = QColor(255, 255, 255, 40)
    fill.go(tint)
    assert fill.colour.rgba() == tint.rgba() and fill.target.rgba() == tint.rgba() and not fill.running and seen
    seen.clear()
    fill.go(QColor(255, 255, 255, 40))
    assert not seen  # the same colour: no news
    fill.go(QColor(0, 0, 0, 0))
    assert fill.colour.alpha() == 0
    fill.go(QColor(9, 9, 9, 0))  # another clear colour is the same to the eye
    assert not fill.running


def test_a_fill_fades_a_tint_in_without_changing_its_hue(qt_app, motion_on):
    fill = motion.Fill()
    tint = QColor(255, 255, 255, 120)
    fill.go(tint, 5000)
    assert fill.running and fill.colour.alpha() == 0
    fill._animation.setCurrentTime(2500)
    now = fill.colour
    assert 0 < now.alpha() < 120 and (now.red(), now.green(), now.blue()) == (255, 255, 255)
    fill.go(QColor(0, 0, 0, 0), 60)  # turned back half way: from the colour it has, not from either end
    assert fill.colour.rgba() == now.rgba() and fill.target.alpha() == 0
    quick = motion.Fill()
    quick.go(tint, 60)
    assert wait_for(lambda: not quick.running, 5) and quick.colour.rgba() == tint.rgba()
    quick.go(QColor(0, 0, 0, 0), 60)
    assert wait_for(lambda: not quick.running, 5) and quick.colour.alpha() == 0


# Panels that appear and slide ------------------------------------------------------------------------------------


@pytest.fixture
def stage(qt_app):
    """A dark page with a red 60 x 30 child at (20, 30): where the child is painted shows in the page's picture."""
    page = QWidget()
    page.resize(300, 200)
    page.setStyleSheet("background: #202020;")
    child = QFrame(page)
    child.setGeometry(20, 30, 60, 30)
    child.setStyleSheet("background: #ff0000;")
    page.show()
    settle()
    yield page, child
    page.close()
    page.deleteLater()


def pixel(page, x, y) -> int:
    return page.grab().toImage().pixelColor(x, y).rgb()


def entrance_of(widget):
    return next(e for e in widget.findChildren(motion._Entrance) if not e._done)


def test_appear_changes_nothing_with_motion_off(stage):
    page, child = stage
    motion.appear(child)
    assert child.graphicsEffect() is None and child.geometry() == QRect(20, 30, 60, 30)
    assert not child.findChildren(motion._Entrance)


def test_appear_fades_and_lifts_only_the_picture(stage, motion_on):
    page, child = stage
    motion.appear(child, rise=8, ms=5000)
    effect, entrance = child.graphicsEffect(), entrance_of(child)
    assert isinstance(effect, QGraphicsOpacityEffect) and effect.opacity() == 0.0
    assert child.isVisible() and child.geometry() == QRect(20, 30, 60, 30)  # in its place from the first frame
    assert pixel(page, 30, 45) == BACKGROUND  # but not painted yet
    entrance._animation.stop()
    entrance._step(0.5)  # half way, at an exact moment rather than by the clock
    assert effect.opacity() == 0.5 and child.geometry() == QRect(20, 30, 60, 30)
    assert pixel(page, 30, 31) == BACKGROUND  # its top rows have not come up yet
    assert pixel(page, 30, 63) != BACKGROUND  # and it still reaches 4 px below its place
    entrance.finish()
    assert child.graphicsEffect() is None
    assert pixel(page, 30, 45) == RED and pixel(page, 30, 63) == BACKGROUND


def test_a_negative_rise_drops_the_picture_in(stage, motion_on):
    page, child = stage
    motion.appear(child, rise=-8, ms=5000)
    entrance = entrance_of(child)
    entrance._animation.stop()
    entrance._step(0.5)
    assert pixel(page, 30, 27) != BACKGROUND and pixel(page, 30, 58) == BACKGROUND  # 4 px above its place
    entrance.finish()


def test_appear_runs_by_the_clock_and_leaves_nothing_behind(stage, motion_on):
    page, child = stage
    motion.appear(child, ms=60)
    assert wait_for(lambda: child.graphicsEffect() is None, 5)
    settle()
    assert not child.findChildren(motion._Entrance) and pixel(page, 30, 45) == RED


def test_appearing_again_finishes_the_one_on_its_way(stage, motion_on):
    page, child = stage
    motion.appear(child, ms=5000)
    first = entrance_of(child)
    motion.appear(child, ms=5000)
    assert first._done and entrance_of(child) is not first
    entrance_of(child).finish()
    assert child.graphicsEffect() is None


def test_nothing_appears_on_a_widget_that_is_not_shown(stage, motion_on):
    page, child = stage
    child.hide()
    motion.appear(child)
    assert child.graphicsEffect() is None and not child.findChildren(motion._Entrance)


@pytest.mark.parametrize("edge, painted, clear", [
    (Qt.Edge.LeftEdge, (10, 45), (50, 45)),  # 60 px sideways: half way, 30 px to the left of its place
    (Qt.Edge.RightEdge, (100, 45), (25, 45)),
    (Qt.Edge.TopEdge, (30, 20), (30, 50)),  # 30 px, its own height: half way, 15 px up
    (Qt.Edge.BottomEdge, (30, 70), (30, 35)),
])
def test_slide_in_comes_from_the_edge_it_opens_from(stage, motion_on, edge, painted, clear):
    page, child = stage
    motion.slide_in(child, edge, ms=5000)
    entrance = entrance_of(child)
    assert pixel(page, 30, 45) == BACKGROUND and child.geometry() == QRect(20, 30, 60, 30)
    entrance._animation.stop()
    entrance._step(0.5)
    assert pixel(page, *painted) != BACKGROUND and pixel(page, *clear) == BACKGROUND
    entrance.finish()
    assert child.graphicsEffect() is None and pixel(page, 30, 45) == RED


def test_what_goes_along_is_painted_with_it(stage, motion_on):
    page, child = stage
    other = QFrame(page)
    other.setGeometry(150, 30, 20, 20)
    other.show()
    motion.appear(child, along=[other], ms=5000)
    assert child.graphicsEffect() is not None and other.graphicsEffect() is not None
    entrance_of(child).finish()
    assert child.graphicsEffect() is None and other.graphicsEffect() is None


def test_a_window_fades_through_its_opacity_and_rises(qt_app, motion_on):
    window = QWidget()
    window.resize(100, 60)
    window.move(200, 150)
    window.show()
    settle()
    motion.appear(window, rise=8, ms=5000)
    entrance = entrance_of(window)
    assert window.windowOpacity() == 0.0 and window.pos() == QPoint(200, 158)
    entrance._animation.stop()
    entrance._step(0.5)
    assert window.windowOpacity() == pytest.approx(0.5, abs=0.01) and window.pos() == QPoint(200, 154)  # 8 bits
    window.move(300, 100)  # placed again meanwhile: that is where it lands
    entrance._step(0.75)
    assert window.pos() == QPoint(300, 102)
    entrance.finish()
    assert window.windowOpacity() == 1.0 and window.pos() == QPoint(300, 100)
    window.close()
    window.deleteLater()


def test_a_window_slides_a_short_way(qt_app, motion_on):
    window = QWidget()
    window.resize(400, 300)
    window.move(100, 100)
    window.show()
    settle()
    motion.slide_in(window, Qt.Edge.LeftEdge, ms=5000)
    assert window.pos() == QPoint(100 - motion.SLIDE_WINDOW, 100)  # nothing clips a window: it keeps near
    entrance_of(window).finish()
    assert window.pos() == QPoint(100, 100) and window.windowOpacity() == 1.0
    window.close()
    window.deleteLater()


def test_appear_on_show_runs_at_every_show_and_only_once_over(qt_app, motion_on):
    popup = QWidget()
    popup.resize(80, 40)
    motion.appear_on_show(popup)
    motion.appear_on_show(popup)
    assert len(popup.findChildren(motion._OnShow)) == 1
    popup.show()
    assert popup.isVisible() and popup.windowOpacity() < 1.0  # shown at once, its picture catching up
    assert wait_for(lambda: popup.windowOpacity() == 1.0, 5)
    popup.hide()
    popup.show()
    assert popup.windowOpacity() < 1.0
    entrance_of(popup).finish()
    popup.close()
    popup.deleteLater()


def test_appear_on_show_does_nothing_with_motion_off(qt_app):
    popup = QWidget()
    motion.appear_on_show(popup)
    popup.show()
    assert popup.windowOpacity() == 1.0 and not popup.findChildren(motion._Entrance)
    popup.close()
    popup.deleteLater()


# Fade -----------------------------------------------------------------------------------------------------------


@pytest.fixture
def faded(qt_app):
    holder = QWidget()
    holder.resize(200, 100)
    child = QWidget(holder)
    child.resize(50, 50)
    holder.show()
    settle()
    yield child, Fade(child)
    holder.close()
    holder.deleteLater()


def test_fade_shows_and_hides_at_once_with_motion_off(faded):
    child, fade = faded
    fade.fade_out()
    assert not child.isVisible() and child.graphicsEffect() is None and not fade.showing
    fade.fade_in()
    assert child.isVisible() and child.graphicsEffect() is None and fade.showing


def test_fade_comes_in_slower_than_it_goes_and_eases_both_ways(faded, motion_on):
    child, fade = faded
    fade.fade_out()
    assert child.isVisible() and isinstance(child.graphicsEffect(), QGraphicsOpacityEffect)  # shown while it fades
    assert fade._animation.duration() == motion.FAST
    assert fade._animation.easingCurve().type() == motion.EASE_IN.type()
    assert wait_for(lambda: not child.isVisible(), 5) and child.graphicsEffect() is None
    fade.fade_in()
    assert child.isVisible() and child.graphicsEffect().opacity() < 1.0
    assert fade._animation.duration() == motion.NORMAL == FADE_MS
    assert fade._animation.easingCurve().type() == motion.EASE_OUT.type()
    assert wait_for(lambda: child.graphicsEffect() is None, 5) and child.isVisible()


# What the controls do with it ------------------------------------------------------------------------------------


@pytest.fixture
def control(qt_app):
    holder = QWidget()
    control = Segmented([("a", "Alpha"), ("b", "Beta"), ("c", "Gamma")], parent=holder)
    holder.resize(300, 40)
    control.resize(288, 32)
    holder.show()
    settle()
    yield control
    holder.close()
    holder.deleteLater()


def box(control, index) -> QRectF:
    return QRectF(control.buttons[index].geometry())


def test_the_chosen_fill_lands_on_the_segment_with_motion_off(control):
    assert control._thumb_rect() == box(control, 0)
    control.set_value("c")
    assert control.current() == 2 and control._thumb_rect() == box(control, 2) and not control._thumb.running
    assert control.lit(control.buttons[2]) == 1.0 and control.lit(control.buttons[0]) == 0.0
    control.set_value("nothing of the kind")
    assert control._thumb_rect() is None and control.lit(control.buttons[2]) == 0.0


def test_the_chosen_fill_slides_between_segments(control, motion_on):
    control.set_value("c")
    assert control.current() == 2 and control.value == "c"  # chosen at once: only the picture slides
    assert control._thumb.running and control._thumb_rect() == box(control, 0)
    control._thumb._animation.setCurrentTime(90)  # half of its 180 ms: half way in, the easing being symmetrical
    middle = control._thumb_rect()
    assert box(control, 0).x() < middle.x() < box(control, 2).x() and middle == box(control, 1)
    assert control.lit(control.buttons[1]) == 1.0 and control.lit(control.buttons[0]) == 0.0
    assert wait_for(lambda: not control._thumb.running, 5) and control._thumb_rect() == box(control, 2)
    control.set_value("nothing of the kind")
    control.set_value("a")  # from none picked the fill just appears
    assert not control._thumb.running and control._thumb_rect() == box(control, 0)


def test_the_chosen_fill_turns_round_where_it_is(control, motion_on):
    control.set_value("c")
    control._thumb._animation.setCurrentTime(90)
    assert control._thumb.value == 1.0
    control.set_value("a")  # sent back half way: from where the fill is, not from either end
    assert control._thumb.value == 1.0 and control._thumb.target == 0.0 and control.current() == 0
    assert wait_for(lambda: not control._thumb.running, 5) and control._thumb_rect() == box(control, 0)


def test_a_segment_under_the_pointer_fades_its_fill(control, motion_on):
    segment = control.buttons[1]
    segment.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
    segment.grab()
    assert segment._hover.target.alpha() > 0 and segment._hover.running
    chosen = control.buttons[0]  # the chosen one needs no hover fill
    chosen.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
    chosen.grab()
    assert chosen._hover.target.alpha() == 0
    segment.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
    chosen.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)


@pytest.fixture
def switch(qt_app):
    holder = QWidget()
    holder.resize(60, 40)
    switch = Switch(False, holder)
    switch.resize(40, 22)
    holder.show()
    settle()
    yield switch
    holder.close()
    holder.deleteLater()


def test_the_knob_lands_at_once_with_motion_off(switch):
    switch.setChecked(True)
    assert switch._travel.value == 1.0 and not switch._travel.running
    switch.setChecked(False)
    assert switch._travel.value == 0.0


def test_the_knob_slides_when_motion_runs(switch, motion_on):
    switch.setChecked(True)
    assert switch.isChecked() and switch._travel.running  # on at once, whatever the picture says
    switch._travel._animation.setCurrentTime(60)  # half of its 120 ms
    assert 0.0 < switch._travel.value < 1.0
    assert wait_for(lambda: not switch._travel.running, 5) and switch._travel.value == 1.0
    switch.setChecked(False)
    switch._travel._animation.setCurrentTime(60)
    midway = switch._travel.value
    switch.setChecked(True)  # turned back half way: the knob turns round where it is
    assert switch._travel.value == midway and switch._travel.target == 1.0
    assert wait_for(lambda: not switch._travel.running, 5) and switch._travel.value == 1.0
    switch.blockSignals(True)
    switch.setChecked(False)  # a change nobody was told of: the knob is drawn where the state says
    switch.blockSignals(False)
    switch.setChecked(True)  # a real one starts from the left, where the state had put the knob, not from a stale place
    assert switch._travel.running and switch._travel.value == 0.0 and switch._travel.target == 1.0


@pytest.fixture
def lone_button(qt_app):
    """An icon button on a floating bar over a page, in Zenith's dark theme with its style sheet on, as in the app."""
    before = themes.current.name, themes.style.key
    themes.choose("dark", "blue", "zenith")
    page = QWidget()
    page.resize(300, 100)
    bar = FloatingBar(page)
    layout = QHBoxLayout(bar)
    layout.setContentsMargins(4, 4, 4, 4)
    button = IconButton("plus", "Add", parent=bar)
    layout.addWidget(button)
    bar.move(20, 20)
    bar.adjustSize()
    page.show()
    settle()
    yield bar, button
    page.close()
    page.deleteLater()
    themes.choose(before[0], "blue", before[1])


def painted(button) -> tuple[int, int, int, int]:
    """The colour at the left of the button, clear of its icon, in a picture of it: its fill, as drawn."""
    return button.grab().toImage().pixelColor(3, button.height() // 2).getRgb()


def near(first, second) -> bool:
    return all(abs(a - b) <= 2 for a, b in zip(first, second))


def hover(button, on: bool) -> None:
    button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, on)  # as the pointer does, without a screen to point at


def test_a_plain_button_on_a_bar_paints_its_hover_and_pressed_fills(lone_button):
    bar, button = lone_button
    t = bar.theme()
    assert button.property("fade") is True and painted(button)[3] == 0
    hover(button, True)
    assert near(painted(button), qcolor(t.glass_hover).getRgb())  # once, not on top of the sheet's own
    button.setDown(True)
    assert near(painted(button), qcolor(t.glass_pressed).getRgb())
    button.setDown(False)
    hover(button, False)
    assert painted(button)[3] == 0


def test_the_painted_fills_are_the_ones_the_sheet_would_draw(lone_button):
    bar, button = lone_button
    hover(button, True)
    mine = [painted(button)]
    button.setDown(True)
    mine.append(painted(button))
    button.setDown(False)
    button.setProperty("fade", False)  # the sheet's own, which the property turned off
    button.style().unpolish(button)
    button.style().polish(button)
    sheets = [painted(button)]
    button.setDown(True)
    sheets.append(painted(button))
    button.setDown(False)
    hover(button, False)
    assert mine[0][3] > 0 and all(near(a, b) for a, b in zip(mine, sheets))


def test_a_button_in_a_window_a_bar_owns_keeps_its_hover_fill(lone_button):
    """The sheet reaches it (a bar's rules are for every button under it), so the painted fill must too."""
    bar, _ = lone_button
    popup = QDialog(bar)
    row = QHBoxLayout(popup)
    inside = IconButton("plus", "Add", parent=popup)
    row.addWidget(inside)
    popup.show()
    settle()
    hover(inside, True)
    assert near(painted(inside), qcolor(bar.theme().glass_hover).getRgb())
    hover(inside, False)
    popup.close()
    popup.deleteLater()


def test_the_fill_takes_the_colours_of_the_bar_it_stands_on(lone_button):
    bar, button = lone_button
    bar.set_tone("light")  # a dark bar over a bright page: the light theme's colours, and its own sheet
    assert bar.theme().name == "light"
    hover(button, True)
    assert near(painted(button), qcolor(bar.theme().glass_hover).getRgb())  # and the bar's sheet adds nothing
    hover(button, False)
    bar.set_tone("")


def test_a_checked_or_disabled_button_has_no_painted_fill_and_a_named_one_keeps_the_sheets(lone_button):
    bar, button = lone_button
    button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
    button.setCheckable(True)
    button.setChecked(True)
    button.grab()
    assert button._fill.target.alpha() == 0  # the sheet fills a checked button
    button.setChecked(False)
    button.setEnabled(False)
    button.grab()
    assert button._fill.target.alpha() == 0
    button.setEnabled(True)
    button.setObjectName("DangerButton")  # it has fills of its own in the sheet
    assert button.property("fade") is False
    button.grab()
    assert button._fill.target.alpha() == 0
    button.setObjectName("")
    assert button.property("fade") is True
    button.grab()
    assert button._fill.target.alpha() > 0
    button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)


def test_the_fills_fade_in_and_out_when_motion_runs(lone_button, motion_on):
    bar, button = lone_button
    hover = qcolor(bar.theme().glass_hover)
    button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, True)
    button.grab()  # the paint says where the fill is heading
    assert button._fill.running and button._fill.colour.alpha() < hover.alpha()
    button._fill._animation.setCurrentTime(30)
    assert 0 < button._fill.colour.alpha() < hover.alpha()
    assert wait_for(lambda: not button._fill.running, 5) and abs(button._fill.colour.alpha() - hover.alpha()) <= 1
    button.setAttribute(Qt.WidgetAttribute.WA_UnderMouse, False)
    button.grab()
    assert button._fill.running and button._fill.colour.alpha() > 0  # it lingers a moment as the pointer leaves
    assert wait_for(lambda: not button._fill.running, 5) and button._fill.colour.alpha() == 0


def test_the_pointer_itself_brings_the_fill_in_and_takes_it_away(lone_button, motion_on):
    bar, button = lone_button
    hover_alpha = qcolor(bar.theme().glass_hover).alpha()
    QTest.mouseMove(button, button.rect().center())
    if not wait_for(button.underMouse, 2):
        pytest.skip("this platform sends no enter events to a window that is not on a screen")
    assert wait_for(lambda: button._fill.target.alpha() == hover_alpha, 5)  # the enter repaints the button
    assert wait_for(lambda: not button._fill.running, 5) and button._fill.colour.alpha() == hover_alpha
    QTest.mouseMove(bar.parentWidget(), QPoint(290, 90))
    assert wait_for(lambda: not button.underMouse(), 2)
    assert wait_for(lambda: button._fill.target.alpha() == 0, 5)  # and so does the leave
    assert wait_for(lambda: not button._fill.running, 5) and button._fill.colour.alpha() == 0


def test_a_fill_turns_round_where_it_is(lone_button, motion_on):
    bar, button = lone_button
    hover(button, True)
    button.grab()
    button._fill._animation.setCurrentTime(30)
    midway = button._fill.colour.alpha()
    assert 0 < midway
    hover(button, False)
    button.grab()
    assert button._fill.colour.alpha() == midway and button._fill.target.alpha() == 0  # fading out from there


def test_a_bars_shadow_rises_and_fades_with_it(lone_button, motion_on):
    bar, _ = lone_button
    shadow = bar._cast_shadow()
    assert shadow is not None
    bar.appear(ms=5000)
    assert bar.graphicsEffect() is not None and shadow.graphicsEffect() is not None
    entrance_of(bar).finish()
    assert bar.graphicsEffect() is None and shadow.graphicsEffect() is None


def test_the_sheets_leave_a_plain_buttons_hover_to_its_painting(qt_app):
    before = themes.current
    try:
        themes.apply(DARK)
        sheet = QApplication.instance().styleSheet()
        assert '#FloatingBar QToolButton[fade="true"]:hover:!checked' in sheet
        assert '#FloatingBar QToolButton[fade="true"]:pressed:!checked' in sheet
        assert '#FloatingBar > QToolButton[fade="true"]:hover:!checked' in tone_sheet(LIGHT)
    finally:
        themes.apply(before)
    assert bar_radius(DARK.look) == 18 and bar_radius(neon.LOOK) == neon.LOOK.panel - 4  # capsules, or the corners


def test_a_dialog_that_appears_on_show_still_opens_centred(qt_app, motion_on):
    parent = QWidget()
    parent.setGeometry(100, 100, 900, 600)
    parent.show()
    dialog = QDialog(parent)
    dialog.resize(440, 140)
    motion.appear_on_show(dialog)
    dialog.show()
    wait_for(lambda: dialog.windowOpacity() == 1.0)
    centre = dialog.frameGeometry().center() - parent.frameGeometry().center()
    assert abs(centre.x()) <= 20 and abs(centre.y()) <= 20  # it used to land at the parent's corner
    dialog.close()
    parent.close()
