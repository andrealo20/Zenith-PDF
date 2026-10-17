"""New versions and the tour of what changed. GitHub is played by fakes: nothing here goes on the network."""

import hashlib
import io
import json

import pytest
from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf import __version__
from zenith_pdf.app import create_app
from zenith_pdf.core import releases, updates
from zenith_pdf.core.updates import Update, UpdateError
from zenith_pdf.settings import Settings
from zenith_pdf.ui.main_window import MainWindow
from zenith_pdf.ui.updates import UpdateCard, Updater, _first_line
from zenith_pdf.ui.whats_new import WhatsNewDialog

FILE = b"MZ the new installer" * 1000
DIGEST = hashlib.sha256(FILE).hexdigest()


class Response(io.BytesIO):
    def __init__(self, data: bytes):
        super().__init__(data)
        self.headers = {"Content-Length": str(len(data))}


def fake_github(version="9.0.0", sums=True):
    names = ["Zenith-PDF-Setup.exe", "Zenith-PDF-macOS.dmg", "Zenith-PDF-Linux-x86_64.AppImage"]
    assets = [{"name": name, "browser_download_url": f"https://example.invalid/{name}", "size": len(FILE)}
              for name in names]
    if sums:
        assets.append({"name": "SHA256SUMS.txt", "browser_download_url": "https://example.invalid/sums"})
    release = {"tag_name": f"v{version}", "body": "## What's Changed\n* Pressure for pens\n",
               "html_url": "https://example.invalid/release", "assets": assets}
    asked = []

    def opener(request, timeout):
        asked.append(request.full_url)
        if request.full_url == updates.LATEST:
            return Response(json.dumps(release).encode())
        if request.full_url.endswith("/sums"):
            return Response("".join(f"{DIGEST}  {name}\n" for name in names).encode())
        return Response(FILE)

    return opener, asked


def test_versions_compare_as_numbers():
    assert releases.version_tuple("v0.10.1") > releases.version_tuple("0.9.9")
    assert releases.version_tuple("0.5") == (0, 5, 0)
    assert [r.version for r in releases.since("0.3.0", "0.5.0")] == ["0.5.0", "0.4.0"]
    assert releases.since(__version__) == []
    assert releases.RELEASES[0].version == __version__  # every version says what it brought


def test_the_latest_release_and_this_systems_file():
    opener, asked = fake_github()
    update = updates.latest(opener, platform="linux")
    assert update.version == "9.0.0" and update.newer
    assert update.name == "Zenith-PDF-Linux-x86_64.AppImage" and update.sha256 == DIGEST
    assert asked[0] == updates.LATEST and all(url.startswith("https://") for url in asked)
    assert updates.latest(opener, platform="win32").name == "Zenith-PDF-Setup.exe"
    unsigned = updates.latest(fake_github(sums=False)[0], platform="darwin")
    assert unsigned.name == "Zenith-PDF-macOS.dmg" and unsigned.sha256 == ""

    def offline(request, timeout):
        raise OSError("no network")

    with pytest.raises(UpdateError):
        updates.latest(offline)
    assert updates.parse_sums(f"{DIGEST} *a.exe\nnot a line\n") == {"a.exe": DIGEST}


def test_a_download_is_kept_only_when_its_fingerprint_matches(tmp_path):
    opener, _ = fake_github()
    update = updates.latest(opener, platform="win32")
    seen = []
    path = updates.download(update, tmp_path, progress=lambda done, total: seen.append((done, total)), opener=opener)
    assert path.read_bytes() == FILE and seen[-1] == (len(FILE), len(FILE))
    forged = Update(**{**update.__dict__, "sha256": "0" * 64})
    with pytest.raises(UpdateError):
        updates.download(forged, tmp_path / "other", opener=opener)
    assert not any((tmp_path / "other").iterdir())  # nothing half-written or wrong stays behind
    with pytest.raises(UpdateError):  # a release without fingerprints is never run
        updates.download(Update(**{**update.__dict__, "sha256": ""}), tmp_path, opener=opener)
    with pytest.raises(UpdateError):
        updates.download(update, tmp_path / "stopped", cancelled=lambda: True, opener=opener)


def test_installing_runs_the_installer_or_swaps_the_appimage(tmp_path, monkeypatch):
    started = []
    setup = tmp_path / "Zenith-PDF-Setup.exe"
    setup.write_bytes(FILE)
    updates.install(setup, "installer", start=lambda program, arguments: started.append((program, arguments)))
    assert started == [(str(setup), "/S /RELAUNCH")]
    updates.install(setup, "installer", relaunch=False, start=lambda p, a: started.append((p, a)))
    assert started[-1][1] == "/S"
    image = tmp_path / "Zenith-PDF.AppImage"
    image.write_bytes(b"old")
    monkeypatch.setenv("APPIMAGE", str(image))
    updates.install(setup, "appimage", start=lambda program, arguments: started.append((program, arguments)))
    assert image.read_bytes() == FILE and started[-1] == (str(image), "")
    with pytest.raises(UpdateError):
        updates.install(setup, "")
    assert updates.install_kind() == ""  # run from source: it can't replace itself


def wait_for(condition, ms=3000):
    for _ in range(ms // 10):
        QApplication.processEvents()
        if condition():
            return True
        QTest.qWait(10)
    return condition()


@pytest.fixture
def settings(tmp_path):
    return Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))


def test_the_updater_tells_skips_and_downloads(qt_app, settings, tmp_path, monkeypatch):
    opener, _ = fake_github()
    monkeypatch.setattr(updates, "downloads_folder", lambda: tmp_path / "downloads")
    updater = Updater(settings, fetch=lambda: updates.latest(opener, platform="win32"),
                      fetch_file=lambda update, folder, **kw: updates.download(update, folder, opener=opener, **kw),
                      kind="installer")
    found, ready, current = [], [], []
    updater.found.connect(found.append)
    updater.ready.connect(ready.append)
    updater.up_to_date.connect(lambda: current.append(True))
    assert updater.due()
    updater.check()
    assert wait_for(lambda: found)
    assert found[0].version == "9.0.0" and not updater.due()  # asked today: not again until tomorrow
    updater.skip()
    found.clear()
    updater.check()
    assert wait_for(lambda: not updater.busy) and not found  # a skipped version stays quiet
    updater.check(manual=True)
    assert wait_for(lambda: found)  # asked by hand, it is shown all the same
    updater.download()
    assert wait_for(lambda: ready)
    assert updater.pending and not updater.relaunch and updater.file.read_bytes() == FILE

    settings.set("update_auto", True)
    automatic = Updater(settings, fetch=lambda: updates.latest(opener, platform="win32"),
                        fetch_file=lambda update, folder, **kw: updates.download(update, folder, opener=opener, **kw),
                        kind="installer")
    done = []
    automatic.ready.connect(done.append)
    automatic.check(manual=True)
    assert wait_for(lambda: done)  # found and downloaded with no one asking

    older = Updater(settings, fetch=lambda: Update(version="0.0.1", notes="", page=""), kind="")
    older.up_to_date.connect(lambda: current.append(True))
    older.check(manual=True)
    assert wait_for(lambda: current)


def test_the_card_offers_what_this_copy_can_do(qt_app, settings):
    from PySide6.QtWidgets import QPushButton, QWidget

    host = QWidget()
    host.resize(900, 600)
    update = Update(version="9.0.0", notes="## What's Changed\n* Pressure for pens\n", page="https://example.invalid",
                    name="Zenith-PDF-Setup.exe", url="https://example.invalid/x", size=1, sha256=DIGEST)
    for kind, expected in (("installer", {"Skip this version", "Later", "Update"}),
                           ("", {"Later", "Open the release page"})):
        updater = Updater(settings, fetch=lambda: update, kind=kind)
        card = UpdateCard(updater, host)
        updater.found.emit(update)
        labels = {button.text() for button in card.findChildren(QPushButton) if button.parent() is card}
        assert card.isVisibleTo(host) and "9.0.0" in card.title.text() and expected <= labels, kind
        assert card.text.text() == "Pressure for pens"
        card.deleteLater()
    assert _first_line("### Full Changelog\n\n") == ""
    host.deleteLater()


def test_the_tour_after_an_update_and_never_on_a_new_install(qt_app, settings, monkeypatch):
    shown = []
    monkeypatch.setattr(MainWindow, "show_whats_new", lambda self, releases=None: shown.append(releases))
    settings.set("remember_position", False)
    _app, window = create_app(["zenith-pdf"], settings)
    try:
        assert not window.after_update() and settings.get("seen_version") == __version__  # a fresh install
        assert not window.after_update()  # and nothing new since
        settings.set("seen_version", "0.4.0")
        assert window.after_update() and shown[-1][0].version == __version__
        settings.set("seen_version", "")
        settings.set("recent", ["C:/old.pdf"])  # files opened: 0.4.0 or earlier, which didn't record it
        assert window.after_update()
        settings.set("seen_version", "0.4.0")
        settings.set("whats_new_after_update", False)
        assert not window.after_update()
    finally:
        window.close()
        window.renderer.shutdown()


def test_the_tour_goes_through_each_highlight_and_shows_it(qt_app):
    dialog = WhatsNewDialog(releases.since("0.4.0"))
    count = len(releases.RELEASES[0].highlights)
    assert dialog.pages.count() == count + 1 and dialog.next.text() == "Show me each one"
    dialog.go(1)
    assert dialog.back.isVisibleTo(dialog) and dialog.next.text() == "Next"
    dialog.go(count)
    assert dialog.next.text() == "Done"
    asked = []
    dialog.show_me.connect(asked.append)
    first = releases.RELEASES[0].highlights[0]
    dialog._show(first.action)
    assert asked == [first.action] and dialog.result() == dialog.DialogCode.Accepted
    dialog.deleteLater()
