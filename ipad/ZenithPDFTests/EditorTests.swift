import PDFKit
import PencilKit
import UIKit
import XCTest
@testable import ZenithPDF

/// A real editor on a real note, in a window of the test host's scene.
@MainActor private final class EditorHarness {
    let document: NoteDocument
    let editor: EditorViewController
    let window: UIWindow

    private init(document: NoteDocument, editor: EditorViewController, window: UIWindow) {
        self.document = document
        self.editor = editor
        self.window = window
    }

    static func open(pages: Int, inWindow: Bool = true, name: String = #function) async throws -> EditorHarness {
        let folder = TestSupport.temporaryFolder(name)
        let url = TestSupport.writeNote(TestSupport.pdf(pages: pages), named: "Note", in: folder)
        let document = try await NoteDocument.open(url, libraryRoot: folder)
        let editor = EditorViewController(document: document)
        let frame = CGRect(x: 0, y: 0, width: 1180, height: 820)
        let window: UIWindow
        if let scene = UIApplication.shared.connectedScenes.compactMap({ $0 as? UIWindowScene }).first {
            window = UIWindow(windowScene: scene)
            window.frame = frame
        } else {
            window = UIWindow(frame: frame)
        }
        let harness = EditorHarness(document: document, editor: editor, window: window)
        if inWindow {
            window.rootViewController = editor
            window.makeKeyAndVisible()
            try await harness.spin(0.5)
        }
        return harness
    }

    /// Lets the main run loop turn: layout, PDFKit's overlays, undo groups closing, the 0.05 s filter delay.
    func spin(_ seconds: Double) async throws {
        try await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }

    func overlay(page index: Int, file: StaticString = #filePath, line: UInt = #line) throws -> PageOverlayView {
        try XCTUnwrap(editor.overlayProvider.overlay(forPageID: document.pageID(at: index)),
                      "no live overlay for page \(index)", file: file, line: line)
    }

    /// What PencilKit does when the person finishes a stroke: the canvas's drawing changes, then the delegate runs.
    func write(_ drawing: PKDrawing, on overlay: PageOverlayView) {
        overlay.canvas.setDrawingSilently(drawing)
        editor.canvasViewDrawingDidChange(overlay.canvas)
    }

    /// Asks the provider for page `index` the way PDFKit does, shows it and, unless `stays`, hides it again.
    @discardableResult func cycle(page index: Int, stays: Bool = false) -> PageOverlayView? {
        let provider = editor.overlayProvider
        guard let page = document.pdf.page(at: index),
              let view = provider.pdfView(editor.pdfView, overlayViewFor: page) else { return nil }
        provider.pdfView(editor.pdfView, willDisplayOverlayView: view, for: page)
        if !stays { provider.pdfView(editor.pdfView, willEndDisplayingOverlayView: view, for: page) }
        return view as? PageOverlayView
    }

    var scrollView: UIScrollView? {
        (editor.pdfView.documentView?.superview as? UIScrollView)
            ?? editor.pdfView.subviews.compactMap { $0 as? UIScrollView }.first
    }

    func close() {
        editor.toolPicker.setVisible(false, forFirstResponder: editor)
        editor.resignFirstResponder()
        window.isHidden = true
        window.rootViewController = nil
    }
}

final class EditorTests: XCTestCase {
    private static let baseDate = Date(timeIntervalSince1970: 1_700_000_000)
    private static let direct = [NSNumber(value: UITouch.TouchType.direct.rawValue)]

    @MainActor private func stroke(y: CGFloat = 100, points: Int = 10, offset: Double = 0) -> PKStroke {
        TestSupport.stroke((0..<points).map { CGPoint(x: 60 + CGFloat($0) * 20, y: y + CGFloat($0 % 2) * 6) },
                           width: 3, date: Self.baseDate + offset)
    }

    // MARK: - Overlays and the canvas cache

    @MainActor func testOverlayPerVisiblePage() async throws {
        let harness = try await EditorHarness.open(pages: 3)
        defer { harness.close() }
        let provider = harness.editor.overlayProvider
        XCTAssertFalse(provider.onScreen.isEmpty, "PDFKit asked for overlays of the pages on screen")
        XCTAssertLessThanOrEqual(provider.overlays.count, 3)
        let ids = Set(harness.document.pages.map(\.id))
        for (id, overlay) in provider.overlays {
            XCTAssertTrue(ids.contains(id))
            XCTAssertEqual(overlay.canvas.pageID, id)
            guard let index = harness.document.index(ofPageID: id) else { continue }
            XCTAssertEqual(overlay.pageSize, harness.document.geometry(at: index).displaySize)
        }
        let first = try harness.overlay(page: 0)
        XCTAssertTrue(provider.onScreen.contains(first.canvas.pageID))
        XCTAssertNotNil(first.superview, "the overlay sits in PDFKit's page view")
        XCTAssertTrue(first.isUserInteractionEnabled)
        XCTAssertTrue(first.superview?.isUserInteractionEnabled ?? false, "pitfall P2")
        XCTAssertTrue((first.canvas.delegate as AnyObject?) === harness.editor)
    }

    @MainActor func testOverlaysKeyedByPageID() async throws {
        let harness = try await EditorHarness.open(pages: 3)
        defer { harness.close() }
        let id = harness.document.pageID(at: 0)
        let before = try harness.overlay(page: 0)
        harness.document.reorderPage(from: 0, to: 2)
        try await harness.spin(0.3)
        XCTAssertEqual(harness.document.index(ofPageID: id), 2)
        let after = try XCTUnwrap(harness.editor.overlayProvider.overlay(forPageID: id))
        XCTAssertTrue(after === before, "the canvas stays with its page")
        let page = try XCTUnwrap(harness.document.pdf.page(at: 2))
        XCTAssertTrue(harness.editor.overlayProvider.pdfView(harness.editor.pdfView, overlayViewFor: page) === before)
        XCTAssertEqual(before.canvas.pageID, id)
    }

    @MainActor func testCanvasCacheIsBounded() async throws {
        let limit = PageOverlayProvider.liveCanvasLimit
        // What PDFKit does while scrolling, step by step: each page shown, then hidden.
        let direct = try await EditorHarness.open(pages: 30, inWindow: false)
        for index in 0..<30 { direct.cycle(page: index) }
        let provider = direct.editor.overlayProvider
        XCTAssertEqual(provider.overlays.count, limit)
        XCTAssertTrue(provider.onScreen.isEmpty)
        let kept = Set((30 - limit..<30).map { direct.document.pageID(at: $0) })
        XCTAssertEqual(Set(provider.overlays.keys), kept, "the least recently shown were dropped")
        // A page on screen is never dropped, however many others come and go.
        let pinned = direct.cycle(page: 0, stays: true)
        for index in 1..<30 { direct.cycle(page: index) }
        XCTAssertTrue(provider.overlay(forPageID: direct.document.pageID(at: 0)) === pinned)
        XCTAssertEqual(provider.overlays.count, limit + 1)

        // And the real thing: scrolling through a note in a window.
        let harness = try await EditorHarness.open(pages: 30)
        defer { harness.close() }
        for index in 0..<30 {
            harness.editor.go(toPage: index, animated: false)
            try await harness.spin(0.03)
        }
        try await harness.spin(0.3)
        let live = harness.editor.overlayProvider
        XCTAssertFalse(live.overlays.isEmpty)
        XCTAssertLessThanOrEqual(live.overlays.count - live.onScreen.count, limit)
    }

    @MainActor func testDroppedCanvasKeepsInkAndUndo() async throws {
        let harness = try await EditorHarness.open(pages: 20, inWindow: false)
        let id = harness.document.pageID(at: 0)
        let overlay = try XCTUnwrap(harness.cycle(page: 0, stays: true))
        harness.write(PKDrawing(strokes: [stroke()]), on: overlay)
        XCTAssertEqual(harness.document.drawing(forPageID: id).strokes.count, 1)
        harness.editor.overlayProvider.pdfView(harness.editor.pdfView, willEndDisplayingOverlayView: overlay,
                                               for: try XCTUnwrap(harness.document.pdf.page(at: 0)))
        for index in 1...PageOverlayProvider.liveCanvasLimit + 1 { harness.cycle(page: index) }
        XCTAssertNil(harness.editor.overlayProvider.overlay(forPageID: id), "page 0's canvas was dropped")
        XCTAssertEqual(harness.document.drawing(forPageID: id).strokes.count, 1, "its ink lives in the document")

        harness.document.undoManager.undo()
        XCTAssertTrue(harness.document.drawing(forPageID: id).strokes.isEmpty, "its undo lives in the note")
        let rebuilt = try XCTUnwrap(harness.cycle(page: 0, stays: true))
        XCTAssertFalse(rebuilt === overlay)
        XCTAssertTrue(rebuilt.canvas.drawing.strokes.isEmpty)
        harness.document.undoManager.redo()
        XCTAssertEqual(rebuilt.canvas.drawing.strokes.count, 1, "redo reaches the new canvas")
    }

    @MainActor func testProviderSetBeforeDocument() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        XCTAssertTrue((harness.editor.pdfView.pageOverlayViewProvider as AnyObject?) === harness.editor.overlayProvider)
        XCTAssertTrue(harness.editor.pdfView.document === harness.document.pdf)
        XCTAssertFalse(harness.editor.overlayProvider.overlays.isEmpty, "overlayViewFor was called (pitfall P1)")
    }

    // MARK: - Coordinates and resolution

    @MainActor func testDisplayLocalMapping() async throws {
        let overlay = PageOverlayView(pageID: UUID(), pageSize: CGSize(width: 600, height: 800))
        overlay.frame = CGRect(x: 0, y: 0, width: 300, height: 400)
        XCTAssertEqual(overlay.displayPoint(fromLocal: CGPoint(x: 150, y: 100)), CGPoint(x: 300, y: 200))
        XCTAssertEqual(overlay.localRect(fromDisplay: CGRect(x: 100, y: 200, width: 60, height: 80)),
                       CGRect(x: 50, y: 100, width: 30, height: 40))
        let local = overlay.localRect(fromDisplay: CGRect(x: 10, y: 20, width: 0, height: 0)).origin
        XCTAssertEqual(overlay.displayPoint(fromLocal: local), CGPoint(x: 10, y: 20))

        // On a real page: a display-space point, through PDF space and PDFKit's view, lands where the canvas draws it.
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let live = try harness.overlay(page: 0)
        let page = try XCTUnwrap(harness.document.pdf.page(at: 0))
        let geometry = harness.document.geometry(at: 0)
        for display in [CGPoint(x: 50, y: 60), CGPoint(x: 300, y: 420), CGPoint(x: 540, y: 790)] {
            let inPDFView = harness.editor.pdfView.convert(geometry.toPDF(display), from: page)
            let back = live.displayPoint(fromLocal: live.convert(inPDFView, from: harness.editor.pdfView))
            XCTAssertEqual(back.x, display.x, accuracy: 1)
            XCTAssertEqual(back.y, display.y, accuracy: 1)
        }
        // The canvas spans the page at its resolution and is stretched onto the overlay.
        let canvas = live.canvas
        XCTAssertEqual(canvas.bounds.width, live.pageSize.width * live.resolution, accuracy: 0.01)
        XCTAssertEqual(canvas.bounds.width * canvas.transform.a, live.bounds.width, accuracy: 0.5)
        XCTAssertEqual(canvas.zoomScale, live.resolution, accuracy: 0.001)
    }

    /// Rows in column x of `image` (scale 1) whose alpha is over `threshold`, and the highest alpha there.
    private func inkRows(_ image: UIImage, column x: Int, threshold: UInt8 = 128) -> (Int, Int) {
        guard let cgImage = image.cgImage else { return (-1, -1) }
        let width = cgImage.width, height = cgImage.height
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        let drawn: Bool = bytes.withUnsafeMutableBytes { buffer in
            guard let space = CGColorSpace(name: CGColorSpace.sRGB),
                  let context = CGContext(data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                                          bytesPerRow: width * 4, space: space,
                                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { return (-1, -1) }
        let column = min(max(x, 0), width - 1)
        let alphas = (0..<height).map { bytes[($0 * width + column) * 4 + 3] }
        return (alphas.filter { $0 > threshold }.count, Int(alphas.max() ?? 0))
    }

    /// Diagnostic: how wide PencilKit draws strokes built in code.
    @MainActor func testInkShownAtItsWidth() async throws {
        func line(_ ink: PKInkingTool.InkType, size: CGFloat, secondsPer10: Double, triple: Bool = false) -> PKStroke {
            var points: [PKStrokePoint] = []
            for x in stride(from: CGFloat(20), through: 180, by: 10) {
                let point = PKStrokePoint(location: CGPoint(x: x, y: 50), timeOffset: Double(x) / 10 * secondsPer10,
                                          size: CGSize(width: size, height: size), opacity: 1, force: 1, azimuth: 0,
                                          altitude: .pi / 2)
                points += triple ? [point, point, point] : [point]
            }
            return PKStroke(ink: PKInk(ink, color: .black), path: PKStrokePath(controlPoints: points, creationDate: Date()),
                            transform: .identity, mask: nil)
        }
        let rect = CGRect(x: 0, y: 0, width: 200, height: 100)
        var report: [String] = []
        for ink in [PKInkingTool.InkType.pen, .marker, .monoline] {
            for dt in [0.001, 0.05, 1.0] {
                var row: [String] = []
                for size in [CGFloat(1), 1.5, 2, 3, 4, 6, 8, 12, 20] {
                    let image = PKDrawing(strokes: [line(ink, size: size, secondsPer10: dt)]).image(from: rect, scale: 4)
                    let half = inkRows(image, column: 400)
                    let faint = inkRows(image, column: 400, threshold: 24)
                    row.append("\(size)=\(half.0)/\(faint.0)/\(half.1)")
                }
                report.append("\(ink.rawValue.split(separator: ".").last ?? "") dt\(dt): " + row.joined(separator: " "))
            }
        }
        let tripled = PKDrawing(strokes: [line(.pen, size: 2, secondsPer10: 0.012, triple: true)]).image(from: rect, scale: 4)
        report.append("pen 2 tripled: \(inkRows(tripled, column: 400))")
        XCTFail("(rows over half alpha / rows visible / max alpha, at scale 4) " + report.joined(separator: " | "))
    }

    @MainActor func testResolutionFormulaAndBudget() async throws {
        let a4 = PaperSize.a4.points
        XCTAssertEqual(PageOverlayView.resolution(onScreenScale: 0.5, pageSize: a4, screenScale: 2), 1)
        XCTAssertEqual(PageOverlayView.resolution(onScreenScale: 2, pageSize: a4, screenScale: 2), 2, accuracy: 0.0001)
        let cap = PageOverlayView.resolution(onScreenScale: 10, pageSize: a4, screenScale: 2)
        XCTAssertEqual(cap, (48_000_000 / (4 * a4.width * a4.height * 4)).squareRoot(), accuracy: 0.0001)
        XCTAssertLessThanOrEqual(4 * a4.width * a4.height * 4 * cap * cap, 48_000_000 * 1.000001)
        XCTAssertLessThan(cap, 10)
        let small = PageOverlayView.resolution(onScreenScale: 10, pageSize: a4, screenScale: 2, budgetBytes: 8_000_000)
        XCTAssertLessThan(small, cap)

        // Measured in window space: a parent's transform counts as much as the overlay's own size.
        let harness = try await EditorHarness.open(pages: 3)
        defer { harness.close() }
        let container = UIView(frame: CGRect(x: 0, y: 0, width: 100, height: 100))
        let probe = PageOverlayView(pageID: UUID(), pageSize: CGSize(width: 100, height: 100))
        probe.frame = container.bounds
        container.addSubview(probe)
        harness.window.addSubview(container)
        container.transform = CGAffineTransform(scaleX: 2.5, y: 2.5)
        XCTAssertEqual(probe.onScreenScale, 2.5, accuracy: 0.001)
        container.removeFromSuperview()

        // The editor gives each overlay on screen what the formula says for its window-space scale.
        harness.editor.setZoom(harness.editor.pdfView.scaleFactorForSizeToFit * 2)
        try await harness.spin(0.6)
        let provider = harness.editor.overlayProvider
        let scale = harness.editor.traitCollection.displayScale > 0 ? harness.editor.traitCollection.displayScale : 2
        XCTAssertFalse(provider.onScreen.isEmpty)
        for (id, overlay) in provider.overlays {
            if provider.onScreen.contains(id) {
                let expected = PageOverlayView.resolution(onScreenScale: overlay.onScreenScale, pageSize: overlay.pageSize,
                                                          screenScale: scale)
                XCTAssertEqual(overlay.resolution, expected, accuracy: 0.01)
                XCTAssertGreaterThan(overlay.resolution, 1, "zoomed in, the canvas draws finer than 1x")
            } else {
                XCTAssertEqual(overlay.resolution, 1)
            }
        }
    }

    // MARK: - Modes, touches, first responder

    @MainActor func testModeTable() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let editor = harness.editor
        let scroll = try XCTUnwrap(harness.scrollView)
        let rows: [(EditorMode, markup: Bool, canvases: Bool, directOnly: Bool)] = [
            (.write, true, true, true), (.text, true, false, false), (.select, true, false, false),
            (.read, false, false, false), (.write, true, true, true),
        ]
        for row in rows {
            editor.mode = row.0
            editor.view.setNeedsLayout()
            editor.view.layoutIfNeeded()
            XCTAssertEqual(editor.pdfView.isInMarkupMode, row.markup, "\(row.0)")
            for overlay in editor.overlayProvider.overlays.values {
                XCTAssertEqual(overlay.canvas.isUserInteractionEnabled, row.canvases, "\(row.0)")
            }
            XCTAssertEqual(scroll.panGestureRecognizer.allowedTouchTypes == Self.direct, row.directOnly, "\(row.0)")
            XCTAssertEqual(editor.singleTap.isEnabled, row.0 != .read, "\(row.0): taps reach the handlers")
            XCTAssertEqual(editor.twoFingerTap.isEnabled, row.0 != .read, "\(row.0)")
            XCTAssertEqual(editor.doubleTap.isEnabled, row.0 == .write, "\(row.0)")
        }
    }

    @MainActor func testPencilNeverScrollsInWriteMode() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let scroll = try XCTUnwrap(harness.scrollView)
        let pencil = NSNumber(value: UITouch.TouchType.pencil.rawValue)
        harness.editor.mode = .write
        // PDFView may rebuild its recognizers on layout; the touch types are set again every time.
        harness.editor.view.setNeedsLayout()
        harness.editor.view.layoutIfNeeded()
        XCTAssertEqual(scroll.panGestureRecognizer.allowedTouchTypes, Self.direct)
        XCTAssertFalse(scroll.panGestureRecognizer.allowedTouchTypes.contains(pencil))
        if let pinch = scroll.pinchGestureRecognizer {
            XCTAssertEqual(pinch.allowedTouchTypes, Self.direct)
        }
        harness.editor.mode = .read
        let defaults = UIScrollView().panGestureRecognizer.allowedTouchTypes
        XCTAssertEqual(scroll.panGestureRecognizer.allowedTouchTypes, defaults, "the recognizer's own default comes back")
    }

    @MainActor func testFingerDrawingTwoFingerScroll() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let editor = harness.editor
        let scroll = try XCTUnwrap(harness.scrollView)
        let overlay = try harness.overlay(page: 0)
        XCTAssertEqual(overlay.canvas.drawingPolicy, .pencilOnly)
        XCTAssertEqual(scroll.panGestureRecognizer.minimumNumberOfTouches, 1)

        editor.fingerDrawing = true
        XCTAssertEqual(overlay.canvas.drawingPolicy, .anyInput, "one finger draws")
        XCTAssertEqual(scroll.panGestureRecognizer.minimumNumberOfTouches, 2, "two fingers scroll")
        XCTAssertFalse(editor.singleTap.isEnabled, "a finger tap is a dot of ink now")
        XCTAssertFalse(editor.doubleTap.isEnabled)
        XCTAssertTrue(editor.twoFingerTap.isEnabled)

        // When the two-finger pan begins, the stroke the first finger started is cancelled.
        harness.write(PKDrawing(strokes: [stroke()]), on: overlay)
        editor.cancelStrokesInProgress()
        XCTAssertTrue(overlay.canvas.drawingGestureRecognizer.isEnabled, "drawing works again right after")
        XCTAssertEqual(harness.document.drawing(forPageID: overlay.canvas.pageID).strokes.count, 1, "finished ink stays")

        let fresh = try XCTUnwrap(harness.cycle(page: 1, stays: true))
        XCTAssertEqual(fresh.canvas.drawingPolicy, .anyInput, "new canvases follow the setting")
        editor.fingerDrawing = false
        XCTAssertEqual(overlay.canvas.drawingPolicy, .pencilOnly)
        XCTAssertEqual(scroll.panGestureRecognizer.minimumNumberOfTouches, 1)
        XCTAssertTrue(editor.singleTap.isEnabled)
    }

    @MainActor func testToolPickerStaysWhenFirstPageScrollsAway() async throws {
        let harness = try await EditorHarness.open(pages: 10)
        defer { harness.close() }
        let editor = harness.editor
        XCTAssertTrue(editor.isFirstResponder, "the editor holds first responder in write mode")
        XCTAssertTrue(editor.toolPicker.isVisible)
        let firstID = harness.document.pageID(at: 0)
        editor.go(toPage: 9, animated: false)
        try await harness.spin(0.5)
        XCTAssertFalse(editor.overlayProvider.onScreen.contains(firstID), "the first page left the screen")
        XCTAssertTrue(editor.isFirstResponder)
        XCTAssertTrue(editor.toolPicker.isVisible, "pitfall P13")

        editor.mode = .text
        try await harness.spin(0.2)
        XCTAssertFalse(editor.toolPicker.isVisible)
        editor.mode = .write
        try await harness.spin(0.2)
        XCTAssertTrue(editor.toolPicker.isVisible, "back in write mode the picker returns")
    }

    // MARK: - Ink and undo

    @MainActor func testDrawingChangeReachesDocument() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let overlay = try harness.overlay(page: 0)
        let id = overlay.canvas.pageID
        harness.write(PKDrawing(strokes: [stroke(), stroke(y: 200, offset: 1)]), on: overlay)
        XCTAssertEqual(harness.document.drawing(forPageID: id).strokes.count, 2)
        XCTAssertTrue(harness.document.isDirty)
        XCTAssertFalse(overlay.canvas.undoManager?.canUndo ?? true, "PencilKit's own undo is cleared")

        // A code assignment is not a change by the person (pitfall P9).
        let revision = harness.document.revision
        overlay.canvas.suppressesChanges = true
        overlay.canvas.drawing = PKDrawing()
        harness.editor.canvasViewDrawingDidChange(overlay.canvas)
        overlay.canvas.suppressesChanges = false
        XCTAssertEqual(harness.document.revision, revision)
        XCTAssertEqual(harness.document.drawing(forPageID: id).strokes.count, 2)
    }

    @MainActor func testDrawingUndoIsDocumentOwned() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let editor = harness.editor
        let overlay = try harness.overlay(page: 0)
        XCTAssertTrue(editor.undoManager === harness.document.undoManager)
        XCTAssertFalse(overlay.canvas.undoManager === harness.document.undoManager)

        harness.write(PKDrawing(strokes: [stroke()]), on: overlay)
        try await harness.spin(0.1)
        XCTAssertTrue(harness.document.undoManager.canUndo)
        editor.undoManager?.undo()
        XCTAssertTrue(harness.document.drawing(forPageID: overlay.canvas.pageID).strokes.isEmpty)
        XCTAssertTrue(overlay.canvas.drawing.strokes.isEmpty, "the canvas shows the undone ink")
        editor.undoManager?.redo()
        XCTAssertEqual(overlay.canvas.drawing.strokes.count, 1)
    }

    @MainActor func testFilterReplacementIsUndoable() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let editor = harness.editor
        let overlay = try harness.overlay(page: 0)
        let id = overlay.canvas.pageID
        let shape = TestSupport.stroke([CGPoint(x: 60, y: 100), CGPoint(x: 240, y: 100)], width: 3, date: Self.baseDate)
        var calls = 0
        editor.drawingFilter = { pageID, old, new, _ in
            calls += 1
            XCTAssertEqual(pageID, id)
            XCTAssertTrue(old.strokes.isEmpty)
            XCTAssertEqual(new.strokes.count, 1)
            return PKDrawing(strokes: [shape])
        }
        harness.write(PKDrawing(strokes: [stroke(points: 10)]), on: overlay)
        try await harness.spin(0.2)
        XCTAssertEqual(calls, 1)
        XCTAssertEqual(harness.document.drawing(forPageID: id).strokes.first?.path.count, 2, "the shape replaced the stroke")
        XCTAssertEqual(overlay.canvas.drawing.strokes.first?.path.count, 2, "and the canvas shows it")

        harness.document.undoManager.undo()
        XCTAssertEqual(harness.document.drawing(forPageID: id).strokes.first?.path.count, 10, "first undo: the raw stroke")
        harness.document.undoManager.undo()
        XCTAssertTrue(harness.document.drawing(forPageID: id).strokes.isEmpty, "second undo: an empty page")
        harness.document.undoManager.redo()
        harness.document.undoManager.redo()
        try await harness.spin(0.2)
        XCTAssertEqual(harness.document.drawing(forPageID: id).strokes.first?.path.count, 2)
        XCTAssertEqual(calls, 1, "redo comes from the note's stack, never from the filter")
    }

    @MainActor func testFilterSkippedWhileUndoing() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let overlay = try harness.overlay(page: 0)
        let id = overlay.canvas.pageID
        var calls = 0
        harness.editor.drawingFilter = { _, _, new, _ in
            calls += 1
            return new
        }
        harness.write(PKDrawing(strokes: [stroke()]), on: overlay)
        harness.document.undoManager.undo()   // before the 0.05 s delay is over
        try await harness.spin(0.2)
        XCTAssertEqual(calls, 0, "the drawing it was meant for is gone")
        XCTAssertTrue(harness.document.drawing(forPageID: id).strokes.isEmpty)
        harness.document.undoManager.redo()
        try await harness.spin(0.2)
        XCTAssertEqual(calls, 0)
        XCTAssertEqual(harness.document.drawing(forPageID: id).strokes.count, 1)

        // A canvas change reported while the note undoes is the undo itself, never a new stroke.
        harness.document.undoManager.registerUndo(withTarget: harness.editor) { editor in
            MainActor.assumeIsolated { () -> Void in
                overlay.canvas.drawing = PKDrawing(strokes: [self.stroke(), self.stroke(y: 300, offset: 2)])
                editor.canvasViewDrawingDidChange(overlay.canvas)
            }
        }
        harness.document.undoManager.undo()
        try await harness.spin(0.2)
        XCTAssertEqual(calls, 0)
        XCTAssertEqual(harness.document.drawing(forPageID: id).strokes.count, 1)
    }

    // MARK: - Taps

    @MainActor func testFingerTapSelectsInWriteMode() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let editor = harness.editor
        let page = try XCTUnwrap(harness.document.pdf.page(at: 0))
        let target = CGPoint(x: 300, y: 400)
        let point = editor.pdfView.convert(target, from: page)
        var taps: [(PDFPage, CGPoint, CGPoint)] = []
        var doubleTaps: [(PDFPage, CGPoint)] = []
        editor.tapHandler = { page, pdfPoint, hostPoint in
            taps.append((page, pdfPoint, hostPoint))
            return true
        }
        editor.doubleTapHandler = { page, pdfPoint in
            doubleTaps.append((page, pdfPoint))
            return true
        }
        editor.mode = .write
        editor.routeTap(at: point)
        XCTAssertEqual(taps.count, 1)
        XCTAssertTrue(taps.first?.0 === page)
        XCTAssertEqual(taps.first?.1.x ?? 0, target.x, accuracy: 0.5)
        XCTAssertEqual(taps.first?.1.y ?? 0, target.y, accuracy: 0.5)
        let host = editor.overlayHost.convert(point, from: editor.pdfView)
        XCTAssertEqual(taps.first?.2 ?? .zero, host)
        editor.routeDoubleTap(at: point)
        XCTAssertEqual(doubleTaps.count, 1)
        XCTAssertEqual(doubleTaps.first?.1.x ?? 0, target.x, accuracy: 0.5)

        editor.fingerDrawing = true
        editor.routeTap(at: point)
        editor.routeDoubleTap(at: point)
        XCTAssertEqual(taps.count, 1, "with finger drawing on a finger writes")
        XCTAssertEqual(doubleTaps.count, 1)
        editor.fingerDrawing = false
        editor.mode = .read
        editor.routeTap(at: point)
        XCTAssertEqual(taps.count, 1, "read mode leaves taps to PDFKit")
        editor.mode = .select
        editor.routeTap(at: point)
        XCTAssertEqual(taps.count, 2)

        // EditorModel's handler: nothing to select in read mode.
        let model = EditorModel(document: harness.document)
        model.mode = .read
        XCTAssertEqual(model.controller.tapHandler?(page, target, .zero), false)
        XCTAssertNotNil(model.controller.doubleTapHandler)
        XCTAssertNotNil(model.controller.drawingFilter)
        XCTAssertNotNil(model.controller.onLastPageWritten)
    }

    @MainActor func testTwoFingerTapUndoes() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let overlay = try harness.overlay(page: 0)
        let id = overlay.canvas.pageID
        harness.write(PKDrawing(strokes: [stroke()]), on: overlay)
        try await harness.spin(0.1)
        harness.editor.mode = .read
        harness.editor.undoFromGesture()
        XCTAssertEqual(harness.document.drawing(forPageID: id).strokes.count, 1, "no undo gesture in read mode")
        harness.editor.mode = .write
        harness.editor.undoFromGesture()
        XCTAssertTrue(harness.document.drawing(forPageID: id).strokes.isEmpty)
        XCTAssertTrue(overlay.canvas.drawing.strokes.isEmpty)
    }

    // MARK: - Looks and presentation

    @MainActor func testCanvasesUseLightAppearance() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        harness.window.overrideUserInterfaceStyle = .dark
        try await harness.spin(0.1)
        XCTAssertEqual(harness.editor.toolPicker.colorUserInterfaceStyle, .light)
        XCTAssertFalse(harness.editor.overlayProvider.overlays.isEmpty)
        for overlay in harness.editor.overlayProvider.overlays.values {
            XCTAssertEqual(overlay.canvas.overrideUserInterfaceStyle, .light)
            XCTAssertEqual(overlay.canvas.traitCollection.userInterfaceStyle, .light, "pitfall P7")
            XCTAssertEqual(overlay.canvas.maximumSupportedContentVersion, .latest)
        }
    }

    @MainActor func testPresentationShownAndCleared() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let editor = harness.editor
        let overlay = try harness.overlay(page: 0)
        let id = overlay.canvas.pageID
        harness.write(PKDrawing(strokes: [stroke(), stroke(y: 300, offset: 1)]), on: overlay)
        let shown = PKDrawing(strokes: [stroke()])
        editor.presentationProvider = { pageID in pageID == id ? shown : nil }
        XCTAssertTrue(editor.isPresenting)
        XCTAssertEqual(overlay.canvas.drawing.strokes.count, 1)
        XCTAssertFalse(overlay.canvas.isUserInteractionEnabled)
        XCTAssertTrue(editor.pdfView.isInMarkupMode, "replay keeps markup mode")

        var tapped: [(Int, CGPoint)] = []
        editor.presentationTapHandler = { index, point in tapped.append((index, point)) }
        let page = try XCTUnwrap(harness.document.pdf.page(at: 0))
        let display = CGPoint(x: 120, y: 200)
        editor.routeTap(at: editor.pdfView.convert(harness.document.geometry(at: 0).toPDF(display), from: page))
        XCTAssertEqual(tapped.first?.0, 0)
        XCTAssertEqual(tapped.first?.1.x ?? 0, display.x, accuracy: 0.5)
        XCTAssertEqual(tapped.first?.1.y ?? 0, display.y, accuracy: 0.5)

        // A canvas change while presenting is not the person's ink.
        editor.canvasViewDrawingDidChange(overlay.canvas)
        XCTAssertEqual(harness.document.drawing(forPageID: id).strokes.count, 2)

        editor.presentationProvider = nil
        XCTAssertFalse(editor.isPresenting)
        XCTAssertEqual(overlay.canvas.drawing.strokes.count, 2)
        XCTAssertTrue(overlay.canvas.isUserInteractionEnabled)
    }

    @MainActor func testHighlightsDrawn() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let overlay = try harness.overlay(page: 0)
        let rect = CGRect(x: 100, y: 120, width: 80, height: 30)
        harness.editor.setHighlights([0: [rect]])
        XCTAssertEqual(overlay.highlights, [rect])
        let layer = try XCTUnwrap(overlay.highlightLayer)
        let drawn = try XCTUnwrap(layer.path).boundingBox
        let expected = overlay.localRect(fromDisplay: rect).insetBy(dx: -1, dy: -1)
        XCTAssertEqual(drawn.minX, expected.minX, accuracy: 0.5)
        XCTAssertEqual(drawn.width, expected.width, accuracy: 0.5)
        XCTAssertTrue(layer.superlayer === overlay.layer)

        // A canvas made later picks up the highlights of its page.
        harness.editor.setHighlights([1: [rect]])
        XCTAssertTrue(overlay.highlights.isEmpty)
        XCTAssertNil(overlay.highlightLayer, "no layer without highlights")
        let other = try XCTUnwrap(harness.cycle(page: 1, stays: true))
        XCTAssertEqual(other.highlights, [rect])
        harness.editor.setHighlights([:])
        XCTAssertTrue(other.highlights.isEmpty)
    }

    @MainActor func testAccessibilityStrokeCount() async throws {
        let harness = try await EditorHarness.open(pages: 2)
        defer { harness.close() }
        let pdfView = harness.editor.pdfView
        XCTAssertEqual(pdfView.accessibilityIdentifier, "editor")
        XCTAssertEqual(pdfView.accessibilityValue, "strokes: 0")
        let overlay = try harness.overlay(page: 0)
        harness.write(PKDrawing(strokes: [stroke()]), on: overlay)
        XCTAssertEqual(pdfView.accessibilityValue, "strokes: 1")
        XCTAssertEqual(harness.editor.strokeCount, 1)
        harness.document.undoManager.undo()
        XCTAssertEqual(pdfView.accessibilityValue, "strokes: 0")
    }

    // MARK: - Volume

    @MainActor func testLargeNoteMemoryAndSave() async throws {
        let harness = try await EditorHarness.open(pages: 400)
        defer { harness.close() }
        let editor = harness.editor
        var peak = TestSupport.physicalFootprint()
        for index in 0..<400 {
            editor.go(toPage: index, animated: false)
            try await harness.spin(0.01)
            if index % 25 == 0 { peak = max(peak, TestSupport.physicalFootprint()) }
        }
        try await harness.spin(0.3)
        peak = max(peak, TestSupport.physicalFootprint())
        print("400 pages walked: peak physical footprint \(peak / 1_000_000) MB")
        XCTAssertLessThan(peak, 1_200_000_000, "physical footprint \(peak) bytes")
        let provider = editor.overlayProvider
        XCTAssertLessThanOrEqual(provider.overlays.count - provider.onScreen.count, PageOverlayProvider.liveCanvasLimit)

        let overlay = try harness.overlay(page: editor.currentPageIndex)
        harness.write(PKDrawing(strokes: [stroke()]), on: overlay)
        // PDFKit's own floor here: read the note's bytes and write them again, as every save must at least do.
        let data = try XCTUnwrap(harness.document.currentPDFData())
        let floorStart = Date()
        _ = PDFDocument(data: data)?.dataRepresentation()
        let floor = Date().timeIntervalSince(floorStart)
        let start = Date()
        await harness.document.save()
        let elapsed = Date().timeIntervalSince(start)
        print("400 pages: \(elapsed) s to save; PDFKit alone rewrites them in \(floor) s")
        XCTAssertNil(harness.document.lastError)
        XCTAssertFalse(harness.document.isDirty)
        // 5 s is the budget on the iPad; a shared runner may take three times PDFKit's own rewrite.
        XCTAssertLessThan(elapsed, max(5, floor * 3), "save took \(elapsed) s (PDFKit alone \(floor) s)")
    }
}
