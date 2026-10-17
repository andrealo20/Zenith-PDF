"""PDF tools, image annotations, redaction, passwords and Compress, with the dialogs answered by the test."""

import io
import random
import shutil
import threading
import time
import zlib

import pikepdf
import pypdfium2 as pdfium
import pytest
import shiboken6
from pikepdf import Array, Dictionary, Name
from PIL import Image, ImageChops, ImageCms, ImageStat
from PySide6.QtCore import QEvent, QSettings, Qt, QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QDialog, QFileDialog

from zenith_pdf.app import create_app
from zenith_pdf.core import images
from zenith_pdf.core import pdf_tools as T
from zenith_pdf.core.annotations import ImageStamp, Redaction, Shape
from zenith_pdf.core.document import Document
from zenith_pdf.settings import Settings
from zenith_pdf.ui import messages, pdf_tools
from zenith_pdf.ui.pdf_tools import ProgressDialog, PropertiesDialog, ToolForm, ToolsPanel, run_tool


def picture(width=60, height=30, color=QColor(220, 40, 40), transparent_half=False) -> bytes:
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(color)
    if transparent_half:
        for x in range(width // 2, width):
            for y in range(height):
                image.setPixelColor(x, y, QColor(0, 0, 0, 0))
    return images.encode(image)


@pytest.fixture
def pdf_copy(sample_pdf, tmp_path):
    path = tmp_path / "doc.pdf"
    shutil.copy(sample_pdf, path)
    return path


# Generated pictures for Compress -----------------------------------------------


def photo(width: int, height: int, seed: int = 1) -> Image.Image:
    """Smooth light with grain, like a photograph; Pillow makes it in a few milliseconds."""
    base = Image.linear_gradient("L").resize((width, height))
    bands = [ImageChops.add(base.rotate(seed * 40 * k, expand=False), Image.effect_noise((width, height), 20), 1, -90)
             for k in range(3)]
    return Image.merge("RGB", bands)


def image_stream(pdf, image: Image.Image, jpeg: bool = False, space=None, **keys) -> pikepdf.Stream:
    """An image XObject holding `image`, as Flate (or JPEG), in its own colour space unless `space` is given."""
    if jpeg:
        buffer = io.BytesIO()
        image.save(buffer, "JPEG", quality=92)
        stream = pikepdf.Stream(pdf, buffer.getvalue())
        stream.Filter = Name.DCTDecode
    else:
        stream = pikepdf.Stream(pdf, zlib.compress(image.tobytes(), 1))
        stream.Filter = Name.FlateDecode
    stream.Type, stream.Subtype = Name.XObject, Name.Image
    stream.Width, stream.Height = image.size
    stream.BitsPerComponent = 8
    stream.ColorSpace = space or {"L": Name.DeviceGray, "RGB": Name.DeviceRGB, "CMYK": Name.DeviceCMYK}[image.mode]
    for key, value in keys.items():
        stream[f"/{key}"] = value
    return pdf.make_indirect(stream)


def build(make) -> bytes:
    """A PDF with one page per picture `make(pdf)` returns, each over most of a blue page, so masks show."""
    pdf = pikepdf.new()
    made = make(pdf)
    for picture_object in made:
        pdf.pages.append(pikepdf.Page(pdf.make_indirect(Dictionary(
            Type=Name.Page, MediaBox=Array([0, 0, 612, 792]),
            Resources=Dictionary(XObject=Dictionary(Im0=picture_object)),
            Contents=pdf.make_stream(b"0 0 1 rg 0 0 612 792 re f q 572 0 0 400 20 200 cm /Im0 Do Q")))))
    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


def renders(data: bytes, scale: float = 0.25) -> list[Image.Image]:
    pdf = pdfium.PdfDocument(data)
    pages = []
    for index in range(len(pdf)):
        page = pdf[index]
        pages.append(page.render(scale=scale).to_pil().convert("RGB"))
        page.close()
    pdf.close()
    return pages


def mean_difference(first: bytes, second: bytes) -> float:
    return max(sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / 3 for a, b in zip(renders(first), renders(second)))


def pictures_of(data: bytes) -> list[dict]:
    found = []
    with pikepdf.open(io.BytesIO(data)) as pdf:
        for obj in pdf.objects:
            if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == Name.Image:
                found.append({"objgen": obj.objgen, "filter": str(obj.get("/Filter")), "size": (int(obj.Width), int(obj.Height)),
                              "space": obj.get("/ColorSpace"), "raw": obj.read_raw_bytes(), "decode": obj.get("/Decode"),
                              "space_name": str(obj.ColorSpace[0]) if isinstance(obj.get("/ColorSpace"), Array)
                              else str(obj.get("/ColorSpace"))})
    return found


# Core ----------------------------------------------------------------------


@pytest.mark.usefixtures("qt_app")
def test_image_annotation_round_trip_on_every_rotation(pdf_copy):
    document = Document(pdf_copy)
    stamps = [ImageStamp(page=i, rect=(40, 50, 120, 60), data=picture(transparent_half=True)) for i in range(4)]
    document.save(stamps)
    document.close()

    reopened = Document(pdf_copy)
    found = sorted(reopened.annotations, key=lambda a: a.page)
    assert [type(a) for a in found] == [ImageStamp] * 4
    for a in found:
        assert a.rect == pytest.approx((40, 50, 120, 60), abs=0.01)
        assert images.size(a.data) == (60, 30)
    reopened.close()

    pdf = pdfium.PdfDocument(str(pdf_copy))
    for index in range(4):
        geometry = Document(pdf_copy).geometry(index)
        image = pdf[index].render(scale=1, draw_annots=True).to_pil().convert("RGB")
        assert image.getpixel((70, 80)) == (220, 40, 40), f"page {index + 1}, rotation {geometry.rotation}"
        assert image.getpixel((140, 80)) == (255, 255, 255)  # the transparent half
    pdf.close()


@pytest.mark.usefixtures("qt_app")
def test_moving_an_image_reuses_its_picture(pdf_copy):
    document = Document(pdf_copy)
    stamp = ImageStamp(page=0, rect=(10, 10, 100, 50), data=picture(400, 200))
    document.save([stamp, ImageStamp(page=1, rect=(0, 0, 50, 25), data=stamp.data)])
    document.close()
    with pikepdf.open(pdf_copy) as pdf:
        pictures = {obj.objgen for obj in pdf.objects if isinstance(obj, pikepdf.Stream) and obj.get("/Subtype") == "/Image"}
    assert len(pictures) == 1  # the same picture twice is stored once


@pytest.mark.usefixtures("qt_app")
def test_photo_becomes_jpeg_screenshot_stays_png():
    noise = random.Random(7)
    width, height = 900, 700
    # Like a photograph: smooth light with fine grain, which PNG compresses badly and JPEG well.
    pixels = bytes(min(255, (x * 255 // width + y * 255 // height) // 2 + noise.randrange(24))
                   for y in range(height) for x in range(width) for _ in range(3))
    photo_image = QImage(pixels, width, height, width * 3, QImage.Format.Format_RGB888)
    assert images.is_jpeg(images.encode(photo_image))
    flat = QImage(900, 700, QImage.Format.Format_RGB32)
    flat.fill(QColor("white"))
    assert images.encode(flat)[:4] == b"\x89PNG"


@pytest.mark.usefixtures("qt_app")
def test_redaction_removes_the_text_underneath(pdf_copy):
    document = Document(pdf_copy)
    hit = next(document.iter_search("needle", False, False))
    mark = Redaction(page=hit.page, rects=hit.rects)
    keep = ImageStamp(page=hit.page, rect=(5, 5, 20, 10), data=picture())
    image = document.render(hit.page, 1.0)
    document.apply_redactions([mark, keep], {hit.page: images.encode(image)})
    assert "needle" not in document.page_text(hit.page)
    assert "needle" in document.page_text(1)  # other pages untouched
    assert [type(a) for a in document.annotations] == [ImageStamp]
    document.close()


@pytest.mark.usefixtures("qt_app")
def test_password_and_permissions_survive_saving(pdf_copy):
    document = Document(pdf_copy)
    document.set_security(T.encryption("secret", "", {"copy": False}), "secret")
    document.save([])
    document.close()
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(pdf_copy)
    with pikepdf.open(pdf_copy, password="secret") as pdf:
        assert not pdf.allow.extract and pdf.allow.print_highres
    reopened = Document(pdf_copy, "secret")
    reopened.set_security(False)
    reopened.save([])
    reopened.close()
    with pikepdf.open(pdf_copy) as pdf:
        assert not pdf.is_encrypted


def test_page_ranges_and_blank_detection():
    assert T.page_ranges([3, 7], 10) == [range(0, 3), range(3, 7), range(7, 10)]
    assert T.chunks(5, 2) == [range(0, 2), range(2, 4), range(4, 5)]
    assert T.blank(b"\xff" * 1000, 0.003)
    assert not T.blank(b"\xff" * 990 + b"\x00" * 10, 0.003)
    assert T.safe_file_name('A: "draft" / v2?') == "A draft v2"


@pytest.mark.usefixtures("qt_app")
def test_pages_per_sheet_and_images_to_pdf(pdf_copy):
    data = T.pages_per_sheet(pdf_copy.read_bytes(), 4, "A4", 10, 5, True)
    pdf = pdfium.PdfDocument(data)
    assert len(pdf) == 1 and pdf.get_page_size(0) == pytest.approx((595.28, 841.89), abs=0.1)
    assert all(f"Page {n} needle" in pdf[0].get_textpage().get_text_range() for n in range(1, 5))
    pdf.close()
    made = T.images_to_pdf([picture(400, 300), picture(300, 400)], "A4", 20)
    pdf = pdfium.PdfDocument(made)
    assert [pdf.get_page_size(i)[0] > pdf.get_page_size(i)[1] for i in range(2)] == [True, False]
    pdf.close()


# Compress, in the engine ---------------------------------------------------------


def test_compress_data_shrinks_pictures_and_reports_progress():
    data = build(lambda pdf: [image_stream(pdf, photo(2600, 1800, 1)), image_stream(pdf, photo(2400, 1600, 2), jpeg=True),
                              image_stream(pdf, photo(300, 200, 3))])
    calls = []
    result = T.compress_data(data, None, 2000, 80, lambda done, total: calls.append((done, total)))
    assert calls == [(0, 3), (1, 3), (2, 3), (3, 3)]
    assert result.changed == 2 and result.left == 0  # the small lossless one stays sharp
    assert len(result.data) < len(data) / 4
    sizes = sorted(p["size"] for p in pictures_of(result.data))
    assert sizes == [(300, 200), (2000, 1333), (2000, 1385)]
    assert mean_difference(data, result.data) < 3
    with pikepdf.open(io.BytesIO(result.data)) as pdf:
        assert not pdf.is_encrypted


def opened_with(data: bytes, password: str) -> str:
    """How a password opens these bytes: "owner", "user" or "no"."""
    try:
        with pikepdf.open(io.BytesIO(data), password=password) as pdf:
            return "owner" if pdf.owner_password_matched else "user"
    except pikepdf.PasswordError:
        return "no"


@pytest.mark.parametrize("revision", [2, 3, 4, 6])
def test_compress_data_keeps_the_encryption_and_both_passwords(revision):
    """Old RC4 files (revisions 2 and 3) included: whichever password opened the file, both still work after."""
    pdf = pikepdf.open(io.BytesIO(build(lambda pdf: [image_stream(pdf, photo(2600, 1800))])))
    buffer = io.BytesIO()
    extra = {"metadata": False, "aes": False} if revision < 4 else {}
    pdf.save(buffer, encryption=pikepdf.Encryption(owner="owner", user="secret", R=revision, **extra))
    pdf.close()
    for typed in ("secret", "owner"):
        result = T.compress_data(buffer.getvalue(), typed, 2000, 80)
        assert result.changed == 1
        assert [opened_with(result.data, p) for p in ("secret", "owner", "")] == ["user", "owner", "no"]
        with pikepdf.open(io.BytesIO(result.data), password="secret") as kept:
            assert kept.encryption.R == revision


@pytest.mark.parametrize("revision", [2, 3])
def test_rebuilt_rc4_encryption_saves(revision):
    """The settings rebuilt for a save from bytes that lost the encryption suit what pikepdf takes for RC4."""
    from zenith_pdf.core.document import _rebuilt_encryption

    buffer = io.BytesIO()
    with pikepdf.new() as pdf:
        pdf.add_blank_page()
        pdf.save(buffer, encryption=pikepdf.Encryption(owner="owner", user="secret", R=revision, metadata=False,
                                                       aes=False))
    data = buffer.getvalue()
    with pikepdf.open(io.BytesIO(data), password="secret") as pdf:
        settings = _rebuilt_encryption(pdf, data, "secret")
        out = io.BytesIO()
        pdf.save(out, encryption=settings)
    assert opened_with(out.getvalue(), "secret") == "user" and opened_with(out.getvalue(), "") == "no"


def test_save_buffer_keeps_every_chunk():
    from zenith_pdf.core.pages import SaveBuffer

    data = build(lambda pdf: [image_stream(pdf, photo(300, 200))])
    with pikepdf.open(io.BytesIO(data)) as pdf:
        buffer, plain = SaveBuffer(), io.BytesIO()
        pdf.save(buffer, deterministic_id=True)
        pdf.save(plain, deterministic_id=True)
    assert buffer.getvalue() == plain.getvalue()


def test_compress_data_stops_when_cancelled():
    data = build(lambda pdf: [image_stream(pdf, photo(2600, 1800, n)) for n in range(3)])
    stop = threading.Event()

    def progress(done, total):
        if done == 1:
            stop.set()  # as Cancel does, from the GUI thread

    assert T.compress_data(data, None, 2000, 80, progress, stop) is None
    stop = threading.Event()
    stop.set()
    assert T.compress_data(data, None, None, None, None, stop) is None


def test_soft_masks_stay_lossless():
    alpha = Image.new("L", (2600, 1800), 0)
    alpha.paste(255, (300, 200, 2300, 1600))  # hard edges: JPEG would ring around them
    small_alpha = alpha.resize((900, 600))

    def make(pdf):
        mask = image_stream(pdf, alpha)  # made first, so it has the lower object number
        big = image_stream(pdf, photo(2600, 1800), SMask=mask)
        small = image_stream(pdf, photo(900, 600), jpeg=True, SMask=image_stream(pdf, small_alpha))
        return [big, small]

    result = T.compress_data(build(make), None, 2000, 80)
    with pikepdf.open(io.BytesIO(result.data)) as pdf:
        parents = {int(o.Width): o for o in pdf.objects if isinstance(o, pikepdf.Stream) and "/SMask" in o}
        assert sorted(parents) == [900, 2000]
        for width, parent in parents.items():
            mask = parent.SMask
            assert str(mask.Filter) == "/FlateDecode"
            values = zlib.decompress(mask.read_raw_bytes())
            if width == 2000:  # shrunk with its picture, by the same resampling
                assert (int(mask.Width), int(mask.Height)) == (2000, 1385)
                assert values == alpha.resize((2000, 1385), Image.LANCZOS).tobytes()
            else:  # the picture kept its size, and so did its mask, sample for sample
                assert (int(mask.Width), int(mask.Height)) == (900, 600)
                assert values == small_alpha.tobytes()


def test_icc_profiles_cmyk_and_16_bit_pictures_keep_their_colours():
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()

    def make(pdf):
        icc = pikepdf.Stream(pdf, profile)
        icc.N = 3
        icc = pdf.make_indirect(icc)
        cmyk = photo(2200, 1500, 4).convert("CMYK")
        eight = photo(2100, 1500, 5).tobytes()
        samples = bytearray(2 * len(eight))
        samples[0::2] = eight  # big-endian: the high byte first
        samples[1::2] = b"\x80" * len(eight)
        wide = pikepdf.Stream(pdf, zlib.compress(bytes(samples), 1))
        wide.Type, wide.Subtype, wide.Filter = Name.XObject, Name.Image, Name.FlateDecode
        wide.Width, wide.Height, wide.BitsPerComponent, wide.ColorSpace = 2100, 1500, 16, Name.DeviceRGB
        return [image_stream(pdf, photo(2200, 1500, 3), space=Array([Name.ICCBased, icc])),
                image_stream(pdf, cmyk),
                image_stream(pdf, cmyk, jpeg=True, Decode=Array([1, 0] * 4)),
                pdf.make_indirect(wide)]

    data = build(make)
    result = T.compress_data(data, None, 2000, 80)
    assert result.changed == 4 and result.left == 0
    found = pictures_of(result.data)
    assert [p["space_name"] for p in found] == ["/ICCBased", "/DeviceCMYK", "/DeviceCMYK", "/DeviceRGB"]
    assert all(p["filter"] == "/DCTDecode" and max(p["size"]) == 2000 for p in found)
    with pikepdf.open(io.BytesIO(result.data)) as pdf:
        spaces = [o.ColorSpace for o in pdf.objects if isinstance(o, pikepdf.Stream) and o.get("/Subtype") == Name.Image]
        assert spaces[0][1].read_bytes() == profile  # the same profile, not a plain DeviceRGB
        images_ = [o for o in pdf.objects if isinstance(o, pikepdf.Stream) and o.get("/Subtype") == Name.Image]
        assert int(images_[3].BitsPerComponent) == 8
    for before_page, after_page in zip(renders(data), renders(result.data)):
        assert sum(ImageStat.Stat(ImageChops.difference(before_page, after_page)).mean) / 3 < 3  # CMYK not inverted


def test_compressing_again_does_not_wear_pictures_down():
    data = build(lambda pdf: [image_stream(pdf, photo(1800, 1200), jpeg=True), image_stream(pdf, photo(2600, 1800))])
    first = T.compress_data(data, None, 2000, 80)
    second = T.compress_data(first.data, None, 2000, 80)
    assert first.changed == 2 and second.changed == 0
    assert [p["raw"] for p in pictures_of(first.data)] == [p["raw"] for p in pictures_of(second.data)]


def test_kinds_compress_cannot_rewrite_are_left_and_counted():
    def make(pdf):
        palette = photo(2600, 1800).convert("P", palette=Image.Palette.ADAPTIVE, colors=16)
        indexed = pikepdf.Stream(pdf, zlib.compress(palette.tobytes()))
        indexed.Type, indexed.Subtype, indexed.Filter = Name.XObject, Name.Image, Name.FlateDecode
        indexed.Width, indexed.Height, indexed.BitsPerComponent = 2600, 1800, 8
        indexed.ColorSpace = Array([Name.Indexed, Name.DeviceRGB, 15, pikepdf.String(bytes(palette.getpalette()[:48]))])
        keyed = image_stream(pdf, photo(2600, 1800), Mask=Array([0, 40, 0, 40, 0, 40]))
        return [pdf.make_indirect(indexed), keyed]

    data = build(make)
    result = T.compress_data(data, None, 2000, 80)
    assert (result.changed, result.left) == (0, 2)
    assert [p["raw"] for p in pictures_of(data)] == [p["raw"] for p in pictures_of(result.data)]


def test_compress_summary_wording():
    assert pdf_tools.compress_summary(1000, 500) == "1000 bytes → 500 bytes (50% smaller) once saved."
    assert "now 0.3% of the original size" in pdf_tools.compress_summary(139_000_000, 417_000)
    assert "(0.5% smaller)" in pdf_tools.compress_summary(1000, 995)
    assert pdf_tools.compress_summary(1000, 1000, 2).endswith("2 large pictures of a kind Compress can't rewrite "
                                                              "stayed as they were.")


# Through the interface --------------------------------------------------------


@pytest.fixture(scope="module")
def window(qt_app, tmp_path_factory):
    store = QSettings(str(tmp_path_factory.mktemp("settings") / "settings.ini"), QSettings.Format.IniFormat)
    _app, window = create_app(["zenith-pdf"], Settings(store))
    window.resize(1200, 850)
    window.show()
    QTest.qWaitForWindowExposed(window)
    yield window
    for index in reversed(range(len(window.workspaces))):
        window.close_tab(index, confirm=False)
    window.close()
    window.renderer.shutdown()


@pytest.fixture
def workspace(window, pdf_copy):
    window.open_file(pdf_copy)
    workspace = window.current_workspace()
    yield workspace
    window.close_tab(window.workspaces.index(workspace), confirm=False)


@pytest.fixture
def answers(monkeypatch, tmp_path):
    """Dialogs answer by themselves: forms with their defaults (or `answers.values`), yes to questions."""

    class Answers:
        values: dict = {}
        folder = tmp_path / "out"
        messages: list = []
        notes: list = []

    Answers.folder.mkdir()

    def run(form: ToolForm):
        assert form._check()
        values = dict(form._values)
        values.update(Answers.values)
        form.deleteLater()
        return values

    monkeypatch.setattr(ToolForm, "run", run)
    monkeypatch.setattr(QFileDialog, "getExistingDirectory", staticmethod(lambda *a, **k: str(Answers.folder)))
    monkeypatch.setattr(messages, "confirm", lambda *a, **k: True)
    monkeypatch.setattr(messages, "inform", lambda parent, title, text, *a, **k: Answers.messages.append(text))
    monkeypatch.setattr(messages, "warn", lambda parent, title, text, *a, **k: Answers.messages.append(text))
    monkeypatch.setattr(pdf_tools, "_show_folder", lambda folder: None)
    return Answers


def test_every_tool_runs(window, workspace, answers):
    """Each tool with its default options, one after the other on the same document."""
    for tool in pdf_tools.TOOLS:
        if tool.key in ("images_to_pdf", "repair", "attachments", "properties", "export_text"):
            continue  # they need a file picked or a dialog of their own; covered below
        run_tool(window, tool.key)
        QApplication.processEvents()
        assert window.current_workspace() is not None, tool.key
    assert any(answers.folder.iterdir())  # split and export wrote files
    assert not answers.messages or all("could not" not in m for m in answers.messages)


def test_page_numbers_tool(window, workspace, answers):
    answers.values = {"custom": "No. {n}/{total}", "pages": [0, 1]}
    run_tool(window, "page_numbers")
    assert "No. 1/2" in workspace.document.page_text(0)
    assert "No. 2/2" in workspace.document.page_text(1)
    assert "No." not in workspace.document.page_text(2)
    workspace.undo_stack.undo()
    assert "No. 1/2" not in workspace.document.page_text(0)


def test_find_and_apply_redactions(window, workspace, answers):
    answers.values = {"text": "needle", "case": False, "word": False, "ocr": False}
    run_tool(window, "redact_find")
    marks = [a for a in workspace.store.all() if isinstance(a, Redaction)]
    assert len(marks) == 4
    run_tool(window, "redact_apply")
    assert all("needle" not in workspace.document.page_text(i) for i in range(4))
    assert not any(isinstance(a, Redaction) for a in workspace.store.all())
    workspace.undo_stack.undo()
    assert "needle" in workspace.document.page_text(0)


def test_paste_and_drop_images(window, workspace):
    image = QImage(200, 100, QImage.Format.Format_RGB32)
    image.fill(QColor(20, 120, 220))
    QApplication.clipboard().setImage(image)
    QApplication.processEvents()
    workspace.view.paste()
    placed = [a for a in workspace.store.all() if isinstance(a, ImageStamp)]
    assert len(placed) == 1
    x, y, w, h = placed[0].rect
    assert w / h == pytest.approx(2.0, rel=0.01)
    assert workspace.view.selected_annotations() == placed  # ready to move
    workspace.undo_stack.undo()
    assert not any(isinstance(a, ImageStamp) for a in workspace.store.all())


def test_compress_and_protect(window, workspace, answers):
    answers.values = {"password": "pw", "again": "pw", "copy": False}
    run_tool(window, "protect")
    workspace.save()
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(workspace.document.path)
    workspace.undo_stack.undo()
    workspace.save()
    with pikepdf.open(workspace.document.path) as pdf:
        assert not pdf.is_encrypted


def test_a_form_stays_open_until_its_values_are_right(window, answers):
    form = ToolForm("Password and permissions", "", window)
    first = form.text("password", "Password", password=True)
    form.text("again", "Again", password=True)
    form.validate = lambda v: "" if v["password"] == v["again"] else "The two passwords are different."
    first.setText("one")
    form.show()
    form.press()  # the main button, as Enter
    assert form.isVisible() and answers.messages[-1] == "The two passwords are different."
    first.setText("")
    form.press()
    assert not form.isVisible() and form.result() == QDialog.DialogCode.Accepted
    form.deleteLater()


def test_forms_are_deleted_after_they_close(window):
    form = ToolForm("Crop pages", "", window)
    form.check("auto", "Trim", True)
    form.exec = lambda: QDialog.DialogCode.Accepted
    form._check()
    assert pdf_tools.ToolForm.run(form) == {"auto": True}
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    assert not shiboken6.isValid(form)


def test_properties_round_trip(workspace):
    document = workspace.document
    assert dict(document.info())["Pages"] == "4"
    workspace.edit_structure("Change properties", lambda a: document.edit_pages(
        a, lambda pdf: T.write_metadata(pdf, {"title": "Notes", "author": "Me", "subject": "", "keywords": ""})))
    assert document.metadata()["title"] == "Notes"
    with pikepdf.open(io.BytesIO(document.data)) as pdf:
        with pdf.open_metadata() as meta:
            assert meta.get("dc:title") == "Notes"


def test_properties_dialog_layout(window, workspace):
    dialog = PropertiesDialog(workspace.document, window)
    assert dialog.width() == pdf_tools.PROPERTIES_WIDTH
    assert dialog.location.toolTip() == str(workspace.document.path.parent)
    assert dialog.button("save").objectName() == "Primary"
    assert dialog.changes() is None
    dialog.fields["title"].setText("A title")
    assert dialog.changes()["title"] == "A title"
    assert pdf_tools.font_summary(["Helvetica (not embedded)", "Arial,Bold"]) == "2 fonts, 1 not embedded"
    assert pdf_tools.font_summary(["Arial"]) == "1 font, embedded"
    dialog.deleteLater()


def test_the_properties_dialog_fits_a_short_screen(window, workspace, monkeypatch):
    """1920 x 1080 at 150% leaves about 672 px above the taskbar: the facts scroll, Save and Cancel stay in sight."""
    from PySide6.QtCore import QRect

    dialog = PropertiesDialog(workspace.document, window)
    facts = dialog.facts.sizeHint().height()
    assert dialog.minimumSizeHint().height() + 31 <= 672
    dialog.deleteLater()
    short = type("Screen", (), {"availableGeometry": lambda self: QRect(0, 0, 1280, 500)})()
    monkeypatch.setattr(PropertiesDialog, "screen", lambda self: short)
    dialog = PropertiesDialog(workspace.document, window)
    assert (dialog.sizeHint().height() <= 500 - pdf_tools.PROPERTIES_CAPTION
            or dialog.facts.wanted == pdf_tools.PROPERTIES_LEAST)
    assert dialog.facts.sizeHint().height() < facts  # they scroll instead
    dialog.deleteLater()


def test_tools_panel_without_a_document(window):
    window.show_home()
    panel = ToolsPanel(window)
    assert panel.testAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
    assert not panel.hint.isHidden()
    tiles = [tile for _, members in panel._groups for tile in members]
    assert {tile.tool.key for tile in tiles if tile.isEnabled()} == {"images_to_pdf", "repair"}
    assert len({tile.height() for tile in tiles}) == 1  # names in a row line up
    panel.close()
    QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete.value)
    assert not shiboken6.isValid(panel)


def test_progress_dialog_cancel_asks_the_job_to_stop(window):
    dialog = ProgressDialog(window, "Compress", "Working", 4)
    stopped = []
    dialog.on_cancel = lambda: stopped.append(True)
    dialog.show()
    dialog.button(messages.CANCEL).click()
    assert dialog.cancelled and stopped == [True] and dialog.isVisible()  # it stays until the job ends
    assert not dialog.button(messages.CANCEL).isEnabled()
    dialog.reject()  # Esc or the close button again: asked only once
    assert stopped == [True]
    dialog.finish()
    assert not dialog.isVisible()


@pytest.fixture
def photo_document(window, tmp_path):
    path = tmp_path / "photos.pdf"
    path.write_bytes(build(lambda pdf: [image_stream(pdf, photo(2600, 1800, n)) for n in range(2)]))
    window.open_file(path)
    workspace = window.current_workspace()
    yield workspace
    window.close_tab(window.workspaces.index(workspace), confirm=False)


def test_compress_through_the_window_and_undo_exactly(window, photo_document, answers, monkeypatch):
    ws = photo_document
    ws.view.add_annotations([Shape(page=0, rect=(100, 100, 80, 60))], "Shape")
    swapped = {}
    replace = ws.replace_structure

    def spy(title, before, after):
        swapped.update(before=before, after=after)
        return replace(title, before, after)

    monkeypatch.setattr(ws, "replace_structure", spy)
    monkeypatch.setattr(ws, "notify", answers.notes.append)
    answers.values = {"level": "balanced"}
    size = len(ws.document.data)
    run_tool(window, "compress")
    assert not ws.busy and len(ws.document.data) < size / 4
    assert ws.undo_stack.undoText() == "Compress" and ws.document.data == swapped["after"]
    assert "smaller) once saved." in answers.notes[-1]
    assert [type(a) for a in ws.store.all()] == [Shape]  # annotations come through
    ws.undo_stack.undo()
    assert ws.document.data is swapped["before"] or ws.document.data == swapped["before"]
    assert [type(a) for a in ws.store.all()] == [Shape]
    ws.undo_stack.redo()
    assert ws.document.data == swapped["after"]


@pytest.mark.parametrize("revision, typed", [(3, "secret"), (6, "owner")])
def test_compress_then_save_keeps_both_passwords(window, tmp_path, answers, monkeypatch, revision, typed):
    """An RC4 file still saves after Compress, and neither password is lost, whichever one opened the file."""
    path = tmp_path / f"locked-{revision}.pdf"
    extra = {"metadata": False, "aes": False} if revision < 4 else {}
    with pikepdf.open(io.BytesIO(build(lambda pdf: [image_stream(pdf, photo(2600, 1800))]))) as pdf:
        pdf.save(path, encryption=pikepdf.Encryption(owner="owner", user="secret", R=revision, **extra))
    monkeypatch.setattr(messages, "get_text", lambda *a, **k: typed)
    window.open_file(path)
    ws = window.current_workspace()
    try:
        monkeypatch.setattr(ws, "notify", answers.notes.append)
        threads = []
        written = type(ws.document).written
        monkeypatch.setattr(type(ws.document), "written",
                            lambda self: (threads.append(threading.current_thread().name), written(self))[1])
        ws.view.add_annotations([Shape(page=0, rect=(100, 100, 80, 60))], "Shape")
        answers.values = {"level": "balanced"}
        run_tool(window, "compress")
        assert ws.undo_stack.undoText() == "Compress"
        assert threads == ["compress"]  # written out by the worker: a large file would stop the window
        ws.save()
    finally:
        window.close_tab(window.workspaces.index(ws), confirm=False)
    data = path.read_bytes()
    assert [opened_with(data, p) for p in ("secret", "owner", "")] == ["user", "owner", "no"]
    reopened = Document(path, "secret")
    assert [type(a) for a in reopened.annotations] == [Shape]
    reopened.close()


def test_cancelled_compress_changes_nothing(window, photo_document, answers, monkeypatch):
    ws = photo_document
    real = T.compress_data

    def pressed_cancel(data, password, side, quality, progress, cancelled):
        cancelled.set()
        return real(data, password, side, quality, progress, cancelled)

    monkeypatch.setattr(pdf_tools.T, "compress_data", pressed_cancel)
    monkeypatch.setattr(ws, "notify", answers.notes.append)
    data, count = ws.document.data, ws.undo_stack.count()
    run_tool(window, "compress")
    assert ws.document.data is data and ws.undo_stack.count() == count and not ws.busy
    assert answers.notes[-1] == "Compress stopped. Nothing was changed."


def test_window_keeps_working_while_a_big_file_compresses(window, tmp_path, answers, monkeypatch):
    """The real path: the 20 ms timer keeps ticking while several megabytes of pictures compress."""
    path = tmp_path / "big.pdf"
    path.write_bytes(build(lambda pdf: [image_stream(pdf, photo(2800, 2000, n)) for n in range(3)]))
    assert path.stat().st_size > 8_000_000
    window.open_file(path)
    ws = window.current_workspace()
    ticks, busy = [], []

    def tick():
        ticks.append(time.perf_counter())
        busy.append(ws.busy)

    timer = QTimer()
    timer.setInterval(20)
    timer.timeout.connect(tick)
    monkeypatch.setattr(ws, "notify", answers.notes.append)
    answers.values = {"level": "balanced"}
    timer.start()
    start = time.perf_counter()
    run_tool(window, "compress")
    end = time.perf_counter()
    timer.stop()
    during = [t for t in ticks if start < t < end]
    gaps = [b - a for a, b in zip(during, during[1:])]
    try:
        assert ws.undo_stack.undoText() == "Compress"
        assert len(during) >= 5 and max(gaps) < 0.5, (len(during), max(gaps, default=0), end - start)
        assert any(busy) and not ws.busy
    finally:
        window.close_tab(window.workspaces.index(ws), confirm=False)
