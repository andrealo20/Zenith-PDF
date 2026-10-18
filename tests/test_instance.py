"""One running Zenith PDF, and zenith-pdf links that open a PDF at a page."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

from PySide6.QtCore import QSettings
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from zenith_pdf.app import create_app
from zenith_pdf.settings import Settings
from zenith_pdf.ui import instance


def test_a_link_names_the_file_and_the_page(tmp_path):
    path = tmp_path / "Lecture notes – week 4 & 5.pdf"
    link = instance.page_link(path, 7)
    assert link.startswith("zenith-pdf://open?file=") and " " not in link
    assert instance.parse_target(link) == (path, 7)
    assert instance.parse_target("zenith-pdf://open?file=x.pdf") == (Path("x.pdf"), None)
    assert instance.parse_target("zenith-pdf://open?page=3") is None
    assert instance.parse_target(str(path)) == (path, None)
    assert instance.parse_target("--reverse") is None



def test_a_link_opens_only_a_pdf_on_this_computer():
    # A link can come from any web page: a network path would hand Windows' sign-in to that server.
    assert instance.parse_target(r"zenith-pdf://open?file=%5C%5Cserver%5Cshare%5Ca.pdf") is None
    assert instance.parse_target("zenith-pdf://open?file=//server/share/a.pdf") is None
    assert instance.parse_target("zenith-pdf://open?file=C%3A%5Cnotes%5Creport.docx") is None
    assert instance.parse_target("zenith-pdf://open?file=C%3A%5Cnotes%5Creport.pdf") == (Path(r"C:\notes\report.pdf"), None)


def test_a_second_start_hands_its_files_to_the_first(qt_app):
    name = f"zenith-pdf-test-{os.getpid()}"
    assert not instance.forward(["a.pdf"], name)  # nothing is running under this name yet
    first = instance.Instance(name=name)
    assert first.listening
    received = []
    first.received.connect(received.extend)
    # A second start, as its own process, while this one keeps answering.
    code = ("import sys; from PySide6.QtCore import QCoreApplication; from zenith_pdf.ui import instance; "
            "app = QCoreApplication([]); "
            f"sys.exit(0 if instance.forward(['a.pdf', 'zenith-pdf://open?file=b.pdf&page=2'], {name!r}) else 3)")
    second = subprocess.Popen([sys.executable, "-c", code], env={**os.environ, "QT_QPA_PLATFORM": "offscreen"})
    for _ in range(500):
        QApplication.processEvents()
        if received and second.poll() is not None:
            break
        QTest.qWait(10)
    assert second.wait(10) == 0  # it heard back, so it can quit
    assert received == ["a.pdf", "zenith-pdf://open?file=b.pdf&page=2"]
    first.deleteLater()


def test_a_link_opens_its_page(qt_app, tmp_path, sample_pdf):
    store = QSettings(str(tmp_path / "settings.ini"), QSettings.Format.IniFormat)
    settings = Settings(store)
    settings.set("remember_position", False)
    _app, window = create_app(["zenith-pdf"], settings)
    window.resize(1200, 800)
    window.show()
    try:
        path = tmp_path / "doc.pdf"
        shutil.copy(sample_pdf, path)
        window.open_targets([instance.page_link(path, 3), "--flag"])
        for _ in range(20):
            QTest.qWait(20)
        workspace = window.current_workspace()
        assert workspace.document.path == path.resolve()
        assert workspace.view.current_page == 2
    finally:
        for index in reversed(range(len(window.workspaces))):
            window.close_tab(index, confirm=False)
        window.close()
        window.renderer.shutdown()



def test_the_installed_program_leaves_the_taskbar_button_to_windows(monkeypatch):
    from zenith_pdf import app

    assert app.taskbar_id() == "ZenithPDF.ZenithPDF"  # from source: apart from other Python programs
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert app.taskbar_id() is None  # installed: the same button as its shortcuts and a pinned one
