"""New versions from GitHub: find the latest release, download this system's file and install it.

Only api.github.com and the release's own files are asked for, over HTTPS, and nothing about the user is
sent. Every download is checked against the release's SHA256SUMS.txt before it runs. How a version
installs depends on how this copy was installed (install_kind):

- "installer": the Windows installer, run silently; it closes nothing, so the app quits first
- "appimage": the AppImage file is replaced where it is, and started again
- "bundle": the .app is replaced from the .dmg, and opened again
- "": run from source, or a copy that can't replace itself: the release page tells what to do
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .. import __version__
from .releases import version_tuple

REPOSITORY = "andrealo20/Zenith-PDF"
LATEST = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPOSITORY}/releases/latest"
ASSETS = {"win32": "Zenith-PDF-Setup.exe", "darwin": "Zenith-PDF-macOS.dmg",
          "linux": "Zenith-PDF-Linux-x86_64.AppImage"}
SUMS = "SHA256SUMS.txt"
TIMEOUT = 15  # seconds for each request
CHUNK = 256 * 1024
CHECK_EVERY = 24 * 60 * 60  # seconds between two automatic checks


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class Update:
    version: str
    notes: str  # the release's own text, Markdown
    page: str  # the release on GitHub
    name: str = ""  # this system's file; "" when the release has none
    url: str = ""
    size: int = 0
    sha256: str = ""  # from SHA256SUMS.txt; "" when the release has no sums

    @property
    def newer(self) -> bool:
        return version_tuple(self.version) > version_tuple(__version__)


def _get(url: str, opener=None, accept: str = "application/vnd.github+json"):
    request = urllib.request.Request(url, headers={"Accept": accept, "User-Agent": f"Zenith-PDF/{__version__}"})
    return (opener or urllib.request.urlopen)(request, timeout=TIMEOUT)


def asset_name(platform: str = sys.platform) -> str:
    return ASSETS.get("linux" if platform.startswith("linux") else platform, "")


def parse_sums(text: str) -> dict[str, str]:
    """`sha256sum` output as {file name: hash}."""
    sums = {}
    for line in text.splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and len(parts[0]) == 64:
            sums[parts[-1].lstrip("*")] = parts[0].lower()
    return sums


def latest(opener=None, platform: str = sys.platform) -> Update:
    """The latest release, with this system's file and its hash. Raises UpdateError when GitHub can't say."""
    try:
        with _get(LATEST, opener) as response:
            release = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as error:
        raise UpdateError(f"GitHub could not be asked for the latest version: {error}") from None
    assets = {asset.get("name", ""): asset for asset in release.get("assets", [])}
    name = asset_name(platform)
    asset = assets.get(name, {})
    sha256 = ""
    if asset and SUMS in assets:
        try:
            with _get(assets[SUMS]["browser_download_url"], opener, "application/octet-stream") as response:
                sha256 = parse_sums(response.read().decode("utf-8", "replace")).get(name, "")
        except OSError:
            sha256 = ""
    return Update(version=str(release.get("tag_name", "")).lstrip("vV"), notes=release.get("body") or "",
                  page=release.get("html_url") or RELEASES_PAGE, name=name if asset else "",
                  url=asset.get("browser_download_url", ""), size=int(asset.get("size") or 0), sha256=sha256)


def install_kind() -> str:
    """How this copy can update itself: "installer", "appimage", "bundle", or "" (it can't)."""
    if not getattr(sys, "frozen", False):
        return ""
    program = Path(sys.executable)
    if sys.platform == "win32":
        return "installer" if (program.parent / "Uninstall.exe").exists() else ""
    if sys.platform == "darwin":
        bundle = app_bundle()
        return "bundle" if bundle is not None and os.access(bundle.parent, os.W_OK) else ""
    image = os.environ.get("APPIMAGE")
    return "appimage" if image and os.access(Path(image).parent, os.W_OK) else ""


def app_bundle() -> Path | None:
    """The .app this copy runs from, on macOS."""
    for parent in Path(sys.executable).resolve().parents:
        if parent.suffix == ".app":
            return parent
    return None


def download(update: Update, folder: Path, progress=None, cancelled=None, opener=None) -> Path:
    """Fetch the update's file into `folder`, checking its SHA-256. `progress(done, total)` is called as it
    comes; `cancelled()` returning True stops it. Raises UpdateError when it fails or doesn't match."""
    if not update.url or not update.sha256:
        raise UpdateError("This release has no checked file for this system.")
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / update.name
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    done = 0
    try:
        with _get(update.url, opener, "application/octet-stream") as response, open(partial, "wb") as out:
            total = int(response.headers.get("Content-Length") or update.size or 0)
            while chunk := response.read(CHUNK):
                if cancelled is not None and cancelled():
                    raise UpdateError("The download was stopped.")
                out.write(chunk)
                digest.update(chunk)
                done += len(chunk)
                if progress is not None:
                    progress(done, total)
    except OSError as error:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"The new version could not be downloaded: {error}") from None
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    if digest.hexdigest() != update.sha256:
        partial.unlink(missing_ok=True)
        raise UpdateError("The downloaded file is not the one the release lists, so it was thrown away.")
    os.replace(partial, target)
    return target


def downloads_folder() -> Path:
    return Path(tempfile.gettempdir()) / "zenith-pdf-update"


def install(path: Path, kind: str, relaunch: bool = True, run=subprocess.run, start=None) -> None:
    """Install the downloaded file. The caller quits the app straight after: the installer waits for it.

    `run` and `start` stand in for subprocess.run and for starting a program, in tests.
    """
    if kind == "installer":
        arguments = "/S /RELAUNCH" if relaunch else "/S"
        if start is not None:
            start(str(path), arguments)
            return
        import ctypes

        # ShellExecute asks for the administrator's permission the installer needs; a plain start can't.
        if ctypes.windll.shell32.ShellExecuteW(None, "open", str(path), arguments, None, 1) <= 32:
            raise UpdateError("The installer could not be started.")
    elif kind == "appimage":
        image = Path(os.environ["APPIMAGE"])
        fresh = image.with_name(f".{image.name}.new")
        shutil.copyfile(path, fresh)
        fresh.chmod(0o755)
        os.replace(fresh, image)  # the running copy keeps its own file until it quits
        if relaunch:
            (start or (lambda program, _args: subprocess.Popen([program], start_new_session=True)))(str(image), "")
    elif kind == "bundle":
        bundle = app_bundle()
        if bundle is None:
            raise UpdateError("This copy does not run from a .app bundle.")
        mount = Path(tempfile.mkdtemp(prefix="zenith-update-"))
        run(["hdiutil", "attach", "-nobrowse", "-readonly", "-noautoopen", "-mountpoint", str(mount), str(path)],
            check=True, capture_output=True)
        try:
            source = next(mount.glob("*.app"))
            fresh = bundle.with_name(bundle.name + ".new")
            shutil.rmtree(fresh, ignore_errors=True)
            run(["ditto", str(source), str(fresh)], check=True, capture_output=True)
        finally:
            run(["hdiutil", "detach", str(mount), "-quiet"], check=False, capture_output=True)
        old = bundle.with_name(bundle.name + f".old-{int(time.time())}")
        os.replace(bundle, old)
        os.replace(fresh, bundle)
        shutil.rmtree(old, ignore_errors=True)
        if relaunch:
            (start or (lambda program, _args: subprocess.Popen(["open", "-n", program])))(str(bundle), "")
    else:
        raise UpdateError("This copy of Zenith PDF can't update itself.")
