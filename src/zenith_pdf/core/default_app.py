"""Zenith PDF as the app that opens PDF files: whether it is, and how it becomes it.

Windows keeps that choice for the user: since Windows 8 a program can't make itself the default (the choice is
signed), so Zenith PDF is listed among the PDF apps (the installer does that) and opens Windows' own page, where
one click confirms. macOS takes the choice from Launch Services, and Linux from xdg-mime, both at once.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from .platforms import package_family

PROG_ID = "ZenithPDF.Document"  # the installer's name for "a PDF Zenith PDF opens"
REGISTERED = "ZenithPDF"  # its entry under RegisteredApplications, which Settings > Default apps lists
BUNDLE_ID = "io.github.andrealo20.zenithpdf"
PDF_UTI = "com.adobe.pdf"
DESKTOP_ID = "zenith-pdf.desktop"
STORE_APP_ID = "ZenithPDF"  # the Application Id in the Store package's manifest
PDF_MIME = "application/pdf"
ICON = Path(__file__).resolve().parents[1] / "assets" / "zenith.ico"

DONE, CONFIRM, FAILED = "done", "confirm", "failed"  # what make_default() achieved


# Windows -----------------------------------------------------------------------------------------------------


class Identity:
    """How Windows knows this copy: an installed Zenith PDF, or one run from source, which has names of its own
    so that it never stands in for the installed one, and both can be picked."""

    def __init__(self, source: bool):
        self.prog_id = "ZenithPDF.Source" if source else PROG_ID
        self.registered = "ZenithPDFSource" if source else REGISTERED
        self.name = "Zenith PDF (source)" if source else "Zenith PDF"
        self.capabilities = rf"Software\{self.registered}\Capabilities"


def identity() -> Identity:
    return Identity(source=not getattr(sys, "frozen", False))


def _windows_choice() -> str:
    """The program the user chose for .pdf files (a ProgId), or "" when Windows decides."""
    import winreg

    base = r"Software\Microsoft\Windows\CurrentVersion\Explorer\FileExts\.pdf"
    for key in ("UserChoiceLatest", "UserChoice"):  # Windows 11 keeps the newer choice in the first
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"{base}\{key}") as opened:
                return str(winreg.QueryValueEx(opened, "ProgId")[0])
        except OSError:
            continue
    return ""


def is_ours(prog_id: str) -> bool:
    return prog_id.lower() in (PROG_ID.lower(), "zenithpdf.source", r"applications\zenith-pdf.exe")


def installed() -> bool:
    """Whether the installer registered Zenith PDF for every user."""
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"Software\RegisteredApplications", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            winreg.QueryValueEx(key, REGISTERED)
        return True
    except OSError:
        return False


def registry_entries(command: list[str], who: Identity, icon: str) -> dict[str, dict[str, str]]:
    """Key -> values that make Windows list `command` among the apps for PDF files, by name and with its logo."""
    line = " ".join(f'"{part}"' for part in command) + ' "%1"'
    about = "Read, mark up and study PDF documents"
    classes = rf"Software\Classes\{who.prog_id}"
    return {
        classes: {"": "PDF document", "FriendlyTypeName": "PDF document"},
        classes + r"\DefaultIcon": {"": icon},
        classes + r"\Application": {"ApplicationName": who.name, "ApplicationIcon": icon,
                                     "ApplicationCompany": "Andrea Loroni", "ApplicationDescription": about},
        classes + r"\shell\open": {"FriendlyAppName": who.name},
        classes + r"\shell\open\command": {"": line},
        r"Software\Classes\.pdf\OpenWithProgids": {who.prog_id: ""},
        who.capabilities: {"ApplicationName": who.name, "ApplicationIcon": icon, "ApplicationDescription": about},
        who.capabilities + r"\FileAssociations": {".pdf": who.prog_id},
        r"Software\RegisteredApplications": {who.registered: who.capabilities},
    }


def register_for_user(command: list[str]) -> bool:
    """For this user, what the installer writes for every one: Zenith PDF among the apps Windows suggests for
    PDF files, with its name and logo. A copy run from source does it at each start (where Python is can change);
    an installed one leaves it to the installer. True when written."""
    if sys.platform != "win32" or (getattr(sys, "frozen", False) and installed()) or package_family():
        return False  # the installer did it, or the Store's package declares it
    import ctypes
    import winreg

    who = identity()
    icon = f"{command[0]},0" if getattr(sys, "frozen", False) else str(ICON)  # never Python's own logo
    entries = registry_entries(command, who, icon)
    if not getattr(sys, "frozen", False) and _source_wrote_installed_names():
        # 0.5.1 before it was released wrote the installed names for a copy run from source, with Python's
        # logo: they get Zenith PDF's, as PDF files may already open with them. The installer takes them away.
        old = Identity(source=False)
        entries |= {path: values for path, values in registry_entries(command, old, icon).items()
                    if old.prog_id in path or path == old.capabilities}
    try:
        for path, values in entries.items():
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, path) as key:
                for name, value in values.items():
                    winreg.SetValueEx(key, name, 0, winreg.REG_SZ, value)
    except OSError:
        return False
    ctypes.windll.shell32.SHChangeNotify(0x08000000, 0, None, None)  # the associations changed
    return True


def _source_wrote_installed_names() -> bool:
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, rf"Software\Classes\{PROG_ID}\shell\open\command") as key:
            return "zenith_pdf" in str(winreg.QueryValueEx(key, "")[0])
    except OSError:
        return False


def windows_page(machine: bool, name: str = REGISTERED) -> str:
    """Windows' Default apps page for Zenith PDF: on Windows 11 it opens with a Set default button at the top."""
    return f"ms-settings:defaultapps?{'registeredAppMachine' if machine else 'registeredAppUser'}={name}"


def _make_default_windows(command: list[str]) -> str:
    """Windows' Default apps page at Zenith PDF: the way when its list of apps can't open."""
    family = package_family()
    if family:  # the Store's copy: its page by the id Windows gives the app in the package
        try:
            os.startfile(f"ms-settings:defaultapps?registeredAUMID={family}!{STORE_APP_ID}")
        except OSError:
            return FAILED
        return CONFIRM
    machine = getattr(sys, "frozen", False) and installed()
    if not machine and not register_for_user(command):
        return FAILED
    try:
        os.startfile(windows_page(machine, REGISTERED if machine else identity().registered))
    except OSError:
        return FAILED
    return CONFIRM


CHOSEN, CANCELLED = "chosen", "cancelled"  # what the list of apps came back with, besides FAILED

# SHOpenWithDialog: offer "Always use this app" (Windows 10) or "Always" (Windows 11), and keep the choice
OAIF_ALLOW_REGISTRATION, OAIF_REGISTER_EXT = 0x1, 0x2
ERROR_CANCELLED_HRESULT = 0x800704C7


def sample_pdf() -> Path:
    """A one-page PDF for Windows' list of apps to be about: it asks for a file, not a type."""
    folder = Path(os.environ.get("TEMP") or Path.home()) / "Zenith PDF"
    path = folder / "Choose the PDF app.pdf"
    if not path.exists():
        folder.mkdir(parents=True, exist_ok=True)
        path.write_bytes(_BLANK_PDF)
    return path


_BLANK_PDF = (b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
              b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 595 842]>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF\n")


def choose_in_windows(window_handle: int) -> str:
    """Windows' own list of apps for PDF files, where Zenith PDF is among the suggested ones: the user picks it
    and Always there, which is the only way a program can become the default. CHOSEN, CANCELLED or FAILED."""
    import ctypes

    class OpenAsInfo(ctypes.Structure):
        _fields_ = [("pcszFile", ctypes.c_wchar_p), ("pcszClass", ctypes.c_wchar_p), ("oaifInFlags", ctypes.c_int)]

    try:
        info = OpenAsInfo(str(sample_pdf()), None, OAIF_ALLOW_REGISTRATION | OAIF_REGISTER_EXT)
        result = ctypes.windll.shell32.SHOpenWithDialog(ctypes.c_void_p(window_handle), ctypes.byref(info))
    except (OSError, AttributeError):
        return FAILED
    if result == 0:
        return CHOSEN
    return CANCELLED if (result & 0xFFFFFFFF) == ERROR_CANCELLED_HRESULT else FAILED


def current_app_name() -> str:
    """The app that opens PDF files now, by the name Windows gives it; "" when it can't tell or elsewhere."""
    if sys.platform != "win32":
        return ""
    import ctypes
    from ctypes import wintypes

    size = wintypes.DWORD(512)
    buffer = ctypes.create_unicode_buffer(size.value)
    try:  # ASSOCF_INIT_IGNOREUNKNOWN, ASSOCSTR_FRIENDLYAPPNAME
        result = ctypes.windll.shlwapi.AssocQueryStringW(0x400, 4, ".pdf", None, buffer, ctypes.byref(size))
    except (OSError, AttributeError):
        return ""
    return buffer.value if result == 0 else ""


def installer_choice() -> bool | None:
    """What the Windows installer was told: True to open PDF files with Zenith PDF (Windows' list of apps opens at
    the first start, as only the user may choose there), False not to, None when it didn't ask."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"Software\ZenithPDF", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as key:
            if not winreg.QueryValueEx(key, "DefaultAsked")[0]:
                return None
            return bool(winreg.QueryValueEx(key, "DefaultPdf")[0])
    except OSError:
        return None


# macOS -------------------------------------------------------------------------------------------------------


def _launch_services():
    """CoreServices and CoreFoundation through ctypes: (services, foundation), or None."""
    import ctypes
    import ctypes.util

    try:
        services = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreServices"))
        foundation = ctypes.cdll.LoadLibrary(ctypes.util.find_library("CoreFoundation"))
    except (OSError, TypeError):
        return None
    foundation.CFStringCreateWithCString.restype = ctypes.c_void_p
    foundation.CFStringCreateWithCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_uint32]
    foundation.CFStringGetCString.restype = ctypes.c_bool
    foundation.CFStringGetCString.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_long, ctypes.c_uint32]
    foundation.CFRelease.argtypes = [ctypes.c_void_p]
    services.LSCopyDefaultRoleHandlerForContentType.restype = ctypes.c_void_p
    services.LSCopyDefaultRoleHandlerForContentType.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    services.LSSetDefaultRoleHandlerForContentType.restype = ctypes.c_int32
    services.LSSetDefaultRoleHandlerForContentType.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p]
    return services, foundation


UTF8 = 0x08000100
ALL_ROLES = 0xFFFFFFFF


def _mac_handler() -> str:
    import ctypes

    found = _launch_services()
    if found is None:
        return ""
    services, foundation = found
    kind = foundation.CFStringCreateWithCString(None, PDF_UTI.encode(), UTF8)
    handler = services.LSCopyDefaultRoleHandlerForContentType(kind, ALL_ROLES)
    foundation.CFRelease(kind)
    if not handler:
        return ""
    buffer = ctypes.create_string_buffer(512)
    ok = foundation.CFStringGetCString(handler, buffer, len(buffer), UTF8)
    foundation.CFRelease(handler)
    return buffer.value.decode() if ok else ""


def _make_default_mac() -> str:
    found = _launch_services()
    if found is None:
        return FAILED
    services, foundation = found
    kind = foundation.CFStringCreateWithCString(None, PDF_UTI.encode(), UTF8)
    bundle = foundation.CFStringCreateWithCString(None, BUNDLE_ID.encode(), UTF8)
    status = services.LSSetDefaultRoleHandlerForContentType(kind, ALL_ROLES, bundle)
    foundation.CFRelease(kind)
    foundation.CFRelease(bundle)
    return DONE if status == 0 else FAILED


# Linux -------------------------------------------------------------------------------------------------------


def data_folder() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def desktop_entry(command: list[str], icon: str) -> str:
    """The desktop entry that opens PDFs and zenith-pdf links with `command`."""
    exec_line = " ".join(f'"{part}"' if " " in part else part for part in command)
    return ("[Desktop Entry]\nType=Application\nName=Zenith PDF\nComment=Read, mark up and study PDF documents\n"
            f"Exec={exec_line} %U\nIcon={icon}\nTerminal=false\nCategories=Office;Viewer;\n"
            f"MimeType={PDF_MIME};x-scheme-handler/zenith-pdf;\nStartupWMClass=Zenith-PDF\n")


def _run(command: list[str]) -> str:
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=10, check=True).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _make_default_linux(command: list[str], run=_run, data: Path | None = None) -> str:
    data = data or data_folder()
    applications = data / "applications"
    # This user's own desktop entry and icon, pointing at this copy: an AppImage unpacks to a folder that goes
    # when it closes, so the icon is copied out of it.
    icon = data / "icons" / "hicolor" / "256x256" / "apps" / "zenith-pdf.png"
    try:
        icon.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(Path(__file__).resolve().parents[1] / "assets" / "zenith.png", icon)
        applications.mkdir(parents=True, exist_ok=True)
        (applications / DESKTOP_ID).write_text(desktop_entry(command, "zenith-pdf"), encoding="utf-8")
    except OSError:
        return FAILED
    run(["update-desktop-database", str(applications)])
    run(["xdg-mime", "default", DESKTOP_ID, PDF_MIME])
    run(["xdg-mime", "default", DESKTOP_ID, "x-scheme-handler/zenith-pdf"])
    return DONE if run(["xdg-mime", "query", "default", PDF_MIME]) == DESKTOP_ID else FAILED


def linux_command(command: list[str]) -> list[str]:
    """How a desktop entry starts this copy: the AppImage itself rather than the folder it unpacked to."""
    appimage = os.environ.get("APPIMAGE")
    return [appimage] if appimage else command


# Any system --------------------------------------------------------------------------------------------------


def can_set() -> bool:
    """Whether this copy can become the default: an app bundle on macOS (a Python run from source isn't one)."""
    if sys.platform == "darwin":
        return bool(getattr(sys, "frozen", False))
    if sys.platform == "win32":
        return True
    return shutil.which("xdg-mime") is not None


def is_default() -> bool | None:
    """Whether PDF files open with Zenith PDF; None when the system doesn't say."""
    try:
        if sys.platform == "win32":
            if package_family():  # the Store's copy goes by a ProgId Windows makes up (AppX and letters)
                return current_app_name() == "Zenith PDF"
            return is_ours(_windows_choice())
        if sys.platform == "darwin":
            handler = _mac_handler()
            return handler.lower() == BUNDLE_ID if handler else None
        answer = _run(["xdg-mime", "query", "default", PDF_MIME])
        return answer == DESKTOP_ID if answer else None
    except Exception:  # a registry or a framework missing: not knowing is fine
        return None


def make_default(command: list[str]) -> str:
    """Ask for Zenith PDF to open PDF files, `command` starting this copy. DONE, CONFIRM (Windows' page is open,
    one click there finishes it) or FAILED."""
    try:
        if sys.platform == "win32":
            return _make_default_windows(command)
        if sys.platform == "darwin":
            return _make_default_mac()
        return _make_default_linux(linux_command(command))
    except Exception:
        return FAILED
