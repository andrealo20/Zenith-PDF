"""The Microsoft Store's copy: its package declares PDF files and links, and the Store updates it."""

import importlib.util
import xml.dom.minidom
from pathlib import Path

from PySide6.QtCore import QSettings

from zenith_pdf.core import default_app
from zenith_pdf.core.i18n import LANGUAGES
from zenith_pdf.settings import Settings
from zenith_pdf.ui import updates
from zenith_pdf.ui.instance import SCHEME

FAMILY = "ZenithPDF_8wekyb3d8bbwe"


def build_script():
    spec = importlib.util.spec_from_file_location("build", Path(__file__).parents[1] / "tools" / "build.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_package_declares_what_the_app_registers_by_itself_elsewhere():
    build = build_script()
    manifest = xml.dom.minidom.parseString(build.store_manifest().encode("utf-8"))
    version = manifest.getElementsByTagName("Identity")[0].getAttribute("Version").split(".")
    assert len(version) == 4 and version[3] == "0"  # what the Store takes
    application = manifest.getElementsByTagName("Application")[0]
    assert application.getAttribute("Id") == default_app.STORE_APP_ID
    assert application.getAttribute("Executable") == f"{build.NAME}.exe"
    types = [node.firstChild.data for node in manifest.getElementsByTagName("uap:FileType")]
    assert types == [".pdf"]
    assert [node.getAttribute("Name") for node in manifest.getElementsByTagName("uap3:Protocol")] == [SCHEME]
    assert set(build.STORE_LANGUAGES) == set(LANGUAGES)  # every language the app speaks, no other
    assert len(manifest.getElementsByTagName("Resource")) == len(LANGUAGES)


def test_the_package_logos_are_drawn_whole_on_a_clear_square():
    build = build_script()
    for name, size, share in build.STORE_LOGOS:
        logo = build.store_logo(size, share)
        assert (logo.size, logo.mode) == ((size, size), "RGBA"), name
        assert logo.getchannel("A").getextrema() == (0, 255), name  # solid somewhere, clear somewhere
        assert logo.getpixel((0, 0))[3] == logo.getpixel((size - 1, size - 1))[3] == 0, name


def test_a_store_copy_leaves_registering_and_updating_to_the_store(monkeypatch, tmp_path):
    monkeypatch.setattr(default_app, "package_family", lambda: FAMILY)
    monkeypatch.setattr(updates, "package_family", lambda: FAMILY)

    def written(*args):
        raise AssertionError("a Store copy wrote what its package declares")

    monkeypatch.setattr(default_app, "registry_entries", written)
    assert default_app.register_for_user(["zenith-pdf"]) is False

    settings = Settings(QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat))
    settings.set("update_check", True)
    settings.set("update_last_check", 0)
    assert not updates.Updater(settings, fetch=lambda: None).due()


def test_a_store_copy_opens_its_own_default_apps_page(monkeypatch):
    monkeypatch.setattr(default_app, "package_family", lambda: FAMILY)
    opened = []
    monkeypatch.setattr(default_app.os, "startfile", opened.append, raising=False)
    assert default_app._make_default_windows(["zenith-pdf"]) == default_app.CONFIRM
    assert opened == [f"ms-settings:defaultapps?registeredAUMID={FAMILY}!{default_app.STORE_APP_ID}"]
