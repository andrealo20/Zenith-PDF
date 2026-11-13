"""The lens of the Liquid glass: a bar's picture with its rim bending the page behind it, drawn on the GPU.

    supported()                    whether this system can have the lens at all: Windows
    start()                        makes the device, the shaders and a first frame, once; True when the lens is up
    available()                    whether the lens is up now; makes nothing
    render(sharp, middle, spec)    the bar as a QImage in device pixels, or None
    shutdown()                     frees everything; runs by itself before the program ends

One graphics device for the whole program (Qt's QRhi over Direct3D 11), never shown anywhere: each bar is drawn
into a texture of its own size and read back. Making the device takes 0.1 to 0.2 s on a graphics card, so
nothing is made at import, and render() draws only once start() has run: start() is for a timer, never for a
paint event. Whatever goes wrong, start() answers False or render() None, the lens stays off for the rest of the
session and says so once in the log; nothing here raises.

The lens works on the main thread, where the application lives; on any other, start() makes nothing and
render() returns None.
"""

from __future__ import annotations

import atexit
import ctypes
import logging
import struct
import sys
import threading
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path

import PySide6
import shiboken6
from PySide6.QtCore import QSize, qInstallMessageHandler
from PySide6.QtGui import QColor, QGuiApplication, QImage

# An older PySide6, which the app allows, may lack one of these: then the lens is not supported, and the app
# still starts, for everyone who never chooses Liquid too.
try:
    from PySide6.QtGui import (
        QRhi,
        QRhiBuffer,
        QRhiColorAttachment,
        QRhiCommandBuffer,
        QRhiDepthStencilClearValue,
        QRhiGraphicsPipeline,
        QRhiReadbackDescription,
        QRhiReadbackResult,
        QRhiSampler,
        QRhiShaderResourceBinding,
        QRhiShaderStage,
        QRhiTexture,
        QRhiTextureRenderTargetDescription,
        QRhiVertexInputAttribute,
        QRhiVertexInputBinding,
        QRhiVertexInputLayout,
        QRhiViewport,
        QShader,
    )
except ImportError:
    _HAS_RHI = False
else:
    _HAS_RHI = True

log = logging.getLogger(__name__)

ASSETS = Path(__file__).resolve().parents[1] / "assets"  # the shaders, baked by tools/shaders.py
KEPT = 16  # pictures whose textures are kept, and sizes of bar whose targets are: eight bars, two pictures each
SLOPE_CAP = 8.0  # where the rim's profile turns vertical
LIGHT_FADE = 1.5  # logical px over which the light on the rim fades, inside the edge

# QRhi::beginOffscreenFrame(QRhiCommandBuffer **, QRhi::BeginFrameFlags) as Windows' compiler names it, and the
# library that has it. The binding's own beginOffscreenFrame() starts the frame and drops the command buffer, so
# the frame is started through the C++ function itself.
_BEGIN = ("Qt6Gui.dll", "?beginOffscreenFrame@QRhi@@QEAA?AW4FrameOpResult@1@PEAPEAVQRhiCommandBuffer@@"
                        "V?$QFlags@W4BeginFrameFlag@QRhi@@@@@Z")
# How a QImage's texels lie in memory. Both go into an RGBA texture as they are; the shader swaps the blue-first.
_BLUE_FIRST = (QImage.Format.Format_RGB32, QImage.Format.Format_ARGB32, QImage.Format.Format_ARGB32_Premultiplied)
_RED_FIRST = (QImage.Format.Format_RGBX8888, QImage.Format.Format_RGBA8888,
              QImage.Format.Format_RGBA8888_Premultiplied)


@dataclass(frozen=True)
class Spec:
    """A bar to draw. Lengths in device pixels, except the look's numbers, which are in logical pixels."""

    width: int
    height: int
    ratio: float = 1.0  # device px per logical px
    radius: float = 0.0  # of the corners; a capsule's is half its height
    chamfer: bool = False  # the top left and bottom right corners cut by `radius`, the other two square
    place: tuple[float, float] = (0.0, 0.0)  # the pictures' top left corner, from the bar's
    area: tuple[float, float] = (1.0, 1.0)  # the width and height the pictures cover
    tint: tuple[int, int, int] = (0, 0, 0)
    rim_veil: float = 0.0  # 0..1: how much of the tint lies over the bent rim
    highlight: tuple[int, int, int, int] = (255, 255, 255, 0)  # the light on the rim, and its strength
    border: tuple[int, int, int, int] = (0, 0, 0, 0)  # the fine edge
    # The look of the bars: one piece of clear glass, a round rim as wide as half the bar that never gives way to a
    # middle, so the page bends across the whole bar, most at its edges, and shows as it is along its axis.
    band: float = 22.0  # the rim that bends: half a bar 44 px tall; each bar passes its own
    thick: float = 8.0  # the glass's thickness: the bend grows with it
    most: float = 6.0  # the bend never moves the sample point further than this
    fringe: float = 0.03  # how far red and blue part from green, as a share of the bend
    profile: str = "circle"  # or "squircle": how the glass rises over the rim
    ease: float = 1.0  # the share of the rim after which it gives way to the middle; from 1 on, never
    shade: float = 0.2  # 0..1: the shadow inside the rim, away from the light
    saturation: float = 1.0  # of what the rim shows


class _Lens:
    """The device and all that lives on it."""

    def __init__(self) -> None:
        self.owned: list = []  # every resource made on the device, oldest first: none may outlive it
        self.textures: OrderedDict[int, tuple[QRhiTexture, bool]] = OrderedDict()  # by QImage.cacheKey()
        self.targets: OrderedDict[tuple[int, int], tuple[QRhiTexture, object]] = OrderedDict()  # by size
        self.bound: tuple | None = None  # the textures the bindings point at
        self.uploads = 0
        self.rhi = None
        try:
            self.build()
        except Exception:
            self.close()  # a lens built halfway holds a device: free it before giving up
            raise

    def build(self) -> None:
        self.rhi = _device()
        if self.rhi is None:
            raise RuntimeError("no graphics device")
        rhi = self.rhi
        self.address = shiboken6.getCppPointer(rhi)[0]
        self.begin = _begin_frame()
        # +1 where the first row read back is the top of clip space, as on Direct3D; -1 where it is the bottom.
        self.up = 1.0 if rhi.isYUpInNDC() != rhi.isYUpInFramebuffer() else -1.0
        self.limit = rhi.resourceLimit(QRhi.ResourceLimit.TextureSizeMax)

        vertex, fragment = _shader("lens.vert.qsb"), _shader("lens.frag.qsb")
        if not (vertex.isValid() and fragment.isValid()):
            raise RuntimeError("the shaders did not load")
        self.corners = self.keep(rhi.newBuffer(QRhiBuffer.Type.Immutable, QRhiBuffer.UsageFlag.VertexBuffer, 32))
        self.uniforms = self.keep(rhi.newBuffer(QRhiBuffer.Type.Dynamic, QRhiBuffer.UsageFlag.UniformBuffer, 144))
        linear, edge = QRhiSampler.Filter.Linear, QRhiSampler.AddressMode.ClampToEdge
        self.sampler = self.keep(rhi.newSampler(linear, linear, QRhiSampler.Filter.None_, edge, edge))
        # A texture of one pixel stands in for the pictures until the first bar, and lends its kind of target
        # to the pipeline: every bar's target is of that kind.
        blank = self.keep(rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(1, 1), 1, QRhiTexture.Flag.RenderTarget))
        first = rhi.newTextureRenderTarget(QRhiTextureRenderTargetDescription(QRhiColorAttachment(blank)))
        self.passes = first.newCompatibleRenderPassDescriptor()
        self.owned.append(self.passes)
        first.setRenderPassDescriptor(self.passes)
        self.keep(first)
        self.bindings = rhi.newShaderResourceBindings()
        self.bind(blank, blank)
        self.owned.append(self.bindings)

        pipeline = rhi.newGraphicsPipeline()
        pipeline.setShaderStages([QRhiShaderStage(QRhiShaderStage.Type.Vertex, vertex),
                                  QRhiShaderStage(QRhiShaderStage.Type.Fragment, fragment)])
        layout = QRhiVertexInputLayout()
        layout.setBindings([QRhiVertexInputBinding(8)])
        layout.setAttributes([QRhiVertexInputAttribute(0, 0, QRhiVertexInputAttribute.Format.Float2, 0)])
        pipeline.setVertexInputLayout(layout)
        pipeline.setTopology(QRhiGraphicsPipeline.Topology.TriangleStrip)
        pipeline.setShaderResourceBindings(self.bindings)
        pipeline.setRenderPassDescriptor(self.passes)
        self.pipeline = self.keep(pipeline)  # the shaders are compiled for the device here

        # The rectangle's corners go up with a first frame, which also proves that frames run at all.
        batch = rhi.nextResourceUpdateBatch()
        batch.uploadStaticBuffer(self.corners, struct.pack("8f", 0, 0, 1, 0, 0, 1, 1, 1))
        commands = self.frame()
        commands.resourceUpdate(batch)
        if rhi.endOffscreenFrame() != QRhi.FrameOpResult.FrameOpSuccess:
            raise RuntimeError("the first frame failed")

    def keep(self, resource):
        """`resource`, built on the device; it goes when the lens does."""
        if not resource.create():
            raise RuntimeError(type(resource).__name__ + " was not created")
        self.owned.append(resource)
        return resource

    def drop(self, resource) -> None:
        self.owned.remove(resource)
        shiboken6.delete(resource)

    def close(self) -> None:
        """Free all of it, the device last. Safe on a lens built halfway."""
        for resource in reversed(self.owned):
            shiboken6.delete(resource)
        self.owned.clear()
        self.textures.clear()
        self.targets.clear()
        if self.rhi is not None:
            shiboken6.delete(self.rhi)
            self.rhi = None

    def frame(self) -> QRhiCommandBuffer:
        """Start an off-screen frame; the caller ends it with rhi.endOffscreenFrame(). The command buffer is the
        device's own: Python cannot tell when it is gone, so it is used within the frame and never kept."""
        slot = ctypes.c_void_p()
        if self.begin(self.address, ctypes.byref(slot), 0) != 0 or not slot.value:
            raise RuntimeError("the frame did not start")
        return shiboken6.wrapInstance(slot.value, QRhiCommandBuffer)

    def bind(self, sharp: QRhiTexture, middle: QRhiTexture) -> None:
        """Point the shader's two pictures at these textures."""
        if self.bound == (sharp, middle):
            return
        stage = QRhiShaderResourceBinding.StageFlag
        self.bindings.setBindings([
            QRhiShaderResourceBinding.uniformBuffer(0, stage.VertexStage | stage.FragmentStage, self.uniforms),
            QRhiShaderResourceBinding.sampledTexture(1, stage.FragmentStage, sharp, self.sampler),
            QRhiShaderResourceBinding.sampledTexture(2, stage.FragmentStage, middle, self.sampler)])
        if not self.bindings.create():
            raise RuntimeError("the bindings were not created")
        self.bound = (sharp, middle)

    def texture(self, image: QImage, batch) -> tuple[QRhiTexture, bool]:
        """The texture that holds `image`, and whether its texels are stored blue first. A picture seen
        before costs nothing; a new one is uploaded, into the texture of the picture used longest ago when
        KEPT are held already."""
        key = image.cacheKey()
        found = self.textures.get(key)
        if found is not None:
            self.textures.move_to_end(key)
            return found
        blue_first = image.format() not in _RED_FIRST
        if blue_first and image.format() not in _BLUE_FIRST:
            image = image.convertToFormat(QImage.Format.Format_RGB32)
        texture = None
        if len(self.textures) >= KEPT:
            texture = self.textures.popitem(last=False)[1][0]
            if texture.pixelSize() != image.size():
                self.bound = None  # the bindings may point at it
                self.drop(texture)
                texture = None
        if texture is None:
            texture = self.keep(self.rhi.newTexture(QRhiTexture.Format.RGBA8, image.size()))
        batch.uploadTexture(texture, image)
        self.uploads += 1
        self.textures[key] = (texture, blue_first)
        return texture, blue_first

    def target(self, width: int, height: int):
        """The texture a bar of this size is drawn into, and the target over it."""
        found = self.targets.get((width, height))
        if found is not None:
            self.targets.move_to_end((width, height))
            return found
        if len(self.targets) >= KEPT:
            for resource in reversed(self.targets.popitem(last=False)[1]):
                self.drop(resource)
        flags = QRhiTexture.Flag.RenderTarget | QRhiTexture.Flag.UsedAsTransferSource
        colour = self.keep(self.rhi.newTexture(QRhiTexture.Format.RGBA8, QSize(width, height), 1, flags))
        target = self.rhi.newTextureRenderTarget(QRhiTextureRenderTargetDescription(QRhiColorAttachment(colour)))
        target.setRenderPassDescriptor(self.passes)
        self.targets[(width, height)] = (colour, self.keep(target))
        return self.targets[(width, height)]

    def render(self, sharp: QImage, middle: QImage, spec: Spec) -> QImage:
        rhi, ratio = self.rhi, spec.ratio
        # Everything that can fail comes before the frame starts: a frame is never left half drawn.
        batch = rhi.nextResourceUpdateBatch()
        sharp_texture, sharp_blue = self.texture(sharp, batch)
        middle_texture, middle_blue = self.texture(middle, batch)
        self.bind(sharp_texture, middle_texture)
        colour, target = self.target(spec.width, spec.height)
        batch.updateDynamicBuffer(self.uniforms, 0, 144, struct.pack(
            "36f",
            spec.width, spec.height, spec.radius, float(spec.chamfer),
            spec.place[0], spec.place[1], 1.0 / spec.area[0], 1.0 / spec.area[1],
            spec.band * ratio, spec.thick * ratio, spec.most * ratio, spec.fringe,
            float(spec.profile == "circle"), spec.ease, SLOPE_CAP, self.up,
            spec.tint[0] / 255, spec.tint[1] / 255, spec.tint[2] / 255, spec.rim_veil,
            *(part / 255 for part in spec.highlight),
            *(part / 255 for part in spec.border),
            # The edge is a whole number of device pixels wide, as the bars' edge is today.
            max(1, int(ratio + 0.5)), LIGHT_FADE * ratio, spec.shade, spec.saturation,
            float(sharp_blue), float(middle_blue), 1.0, 0.0))
        read = QRhiReadbackResult()
        after = rhi.nextResourceUpdateBatch()
        after.readBackTexture(QRhiReadbackDescription(colour), read)

        commands = self.frame()
        try:  # whatever happens in between, the pass and the frame are closed before anything is freed
            commands.beginPass(target, _BLACK, QRhiDepthStencilClearValue(1.0, 0), batch)
            try:
                commands.setGraphicsPipeline(self.pipeline)
                commands.setViewport(QRhiViewport(0, 0, spec.width, spec.height))
                commands.setShaderResources(self.bindings)
                commands.setVertexInput(0, [(self.corners, 0)])
                commands.draw(4)
            finally:
                commands.endPass(after)
        finally:
            ended = rhi.endOffscreenFrame()  # the picture is read back by the time this returns
        if ended != QRhi.FrameOpResult.FrameOpSuccess:
            raise RuntimeError("the frame ended with " + ended.name)
        data = read.data.data()  # bytes of its own: `read` goes with this call
        if len(data) != 4 * spec.width * spec.height:
            raise RuntimeError("the picture read back has the wrong size")
        # The shader wrote blue first, the order of RGB32 in memory. The QImage keeps `data` alive.
        return QImage(data, spec.width, spec.height, 4 * spec.width, QImage.Format.Format_RGB32)


_BLACK = QColor(0, 0, 0)
_lens: _Lens | None = None
_failed = False  # for good, after a failure
_at_exit = False


def _device():
    """Direct3D's device, with no window: the graphics card's, or Windows' software one where that cannot be
    made (not seen for real: this PC makes both)."""
    from PySide6.QtGui import QRhiD3D11InitParams  # Windows alone has it

    params = QRhiD3D11InitParams()
    return (QRhi.create(QRhi.Implementation.D3D11, params)
            or QRhi.create(QRhi.Implementation.D3D11, params, QRhi.Flag.PreferSoftwareRenderer))


def _begin_frame():
    """QRhi::beginOffscreenFrame as a plain function: (the QRhi, where to put the command buffer, flags)."""
    library, symbol = _BEGIN
    kernel = ctypes.WinDLL("kernel32")  # the copy PySide6 has loaded, wherever a packaged app keeps it
    kernel.GetModuleHandleW.restype = ctypes.c_void_p
    kernel.GetModuleHandleW.argtypes = (ctypes.c_wchar_p,)
    handle = kernel.GetModuleHandleW(library)
    # PyDLL keeps the interpreter's lock through the call, as the binding's own calls do.
    function = getattr(ctypes.PyDLL(str(Path(PySide6.__file__).resolve().parent / library), handle=handle), symbol)
    function.argtypes = (ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p), ctypes.c_int)
    function.restype = ctypes.c_int
    return function


def _shader(name: str) -> QShader:
    return QShader.fromSerialized((ASSETS / name).read_bytes())


def _fail(error: Exception, said: list[str] | None = None) -> None:
    """Turn the lens off for the rest of the session; the first time, say why in the log, with what Qt had to
    say (`said`) when it said anything."""
    global _failed
    if not _failed:
        log.warning("the Liquid glass is off for this session: %s: %s%s", type(error).__name__, error,
                    " (Qt: %s)" % "; ".join(said) if said else "")
    _failed = True
    shutdown()


def supported() -> bool:
    """Whether this system can have the lens at all: it draws through Direct3D, and the call that starts a
    frame was tried on Windows alone, with a PySide6 that has all of QRhi this module needs."""
    return sys.platform == "win32" and _HAS_RHI


def available() -> bool:
    """Whether the lens is up, so that render() draws. Cheap, and it makes nothing: start() does."""
    return _lens is not None


def start() -> bool:
    """Make the device, the shaders and a first frame, unless they are there already; True when the lens is up.
    This takes 0.1 to 0.2 s on a graphics card. False, and nothing is tried, where the lens is not supported,
    before there is an application, on any thread but the main one (the device answers only to the thread that
    made it), for good after a failure, and once the application is about to quit (it says so to the lens when
    the first device is made: before that there is nothing to free, and the bars ask for the device from a timer,
    which no longer runs then)."""
    global _lens, _at_exit
    app = QGuiApplication.instance()
    if (_failed or not supported() or not isinstance(app, QGuiApplication)
            or threading.current_thread() is not threading.main_thread()):
        return False
    # A sample or a paint while the program closes must not build a device it is about to free.
    if app.property("lensQuitting") or QGuiApplication.closingDown():
        return False
    if _lens is None:
        said: list[str] = []  # what Qt has to say meanwhile goes into the log's one line, not to stderr
        before = qInstallMessageHandler(lambda kind, context, text: said.append(text))
        try:
            _lens = _Lens()
        except Exception as error:  # whatever it is, the bars fall back to the plain glass
            _fail(error, said)
            return False
        finally:
            qInstallMessageHandler(before)
        if said:
            log.debug("Qt, while the lens was made: %s", "; ".join(said))
        # The device must go before the application does: when it is about to quit, when it is destroyed
        # without quitting, and when the interpreter ends with neither.
        if not app.property("lensGoesWithIt"):
            app.setProperty("lensGoesWithIt", True)
            app.aboutToQuit.connect(_quitting)
            app.destroyed.connect(lambda *_: shutdown())
        if not _at_exit:
            atexit.register(shutdown)
            _at_exit = True
    return True


def render(sharp: QImage, middle: QImage, spec: Spec) -> QImage | None:
    """The bar `spec` describes over the two pictures of what lies behind it: `sharp` as it is, which the rim
    bends, and `middle`, which shows past the rim where it gives way (spec.ease under 1), both covering spec.area
    from spec.place. Opaque, in Format_RGB32, spec.width by
    spec.height. None when the lens is not up or cannot draw it; it never starts the lens."""
    try:
        if _lens is None or threading.current_thread() is not threading.main_thread():
            return None
        if (spec.width < 1 or spec.height < 1 or sharp.isNull() or middle.isNull() or min(spec.area) <= 0
                or spec.ratio <= 0 or max(spec.width, spec.height, sharp.width(), sharp.height(), middle.width(),
                                          middle.height()) > _lens.limit):
            return None  # nothing to draw, or more than the device takes: no fault of the lens
        return _lens.render(sharp, middle, spec)
    except Exception as error:  # a frame that fails, a device that is lost: the bars fall back to the plain glass
        _fail(error)
        return None


def _quitting() -> None:
    """The application is about to quit: free the device, and make no other while the program closes."""
    app = QGuiApplication.instance()
    if app is not None:
        app.setProperty("lensQuitting", True)
    shutdown()


def shutdown() -> None:
    """Free the device and all on it. Runs when the application is about to quit or is destroyed and when the
    interpreter ends, whichever comes first; start() makes a new device afterwards, unless the lens has failed or
    the application is quitting."""
    global _lens
    lens, _lens = _lens, None
    if lens is not None:
        try:
            lens.close()
        except Exception:  # there is nothing left to do about it: the program goes on, or ends, without the lens
            pass
