import PDFKit
import PencilKit
import UIKit
import XCTest
@testable import ZenithPDF

final class FormatTests: XCTestCase {
    private let baseDate = Date(timeIntervalSinceReferenceDate: 800_000_000)

    // MARK: Helpers

    private func wave(from start: CGPoint, count: Int, step: CGFloat = 6, height: CGFloat = 8) -> [CGPoint] {
        (0..<count).map { i in
            CGPoint(x: start.x + CGFloat(i) * step, y: start.y + sin(CGFloat(i) / 3) * height)
        }
    }

    /// Pen, marker and pencil strokes of several widths and colours, a pixel-erased stroke and a one-point dot.
    private func sampleDrawing() -> PKDrawing {
        let pen = TestSupport.stroke(wave(from: CGPoint(x: 60, y: 80), count: 30), width: 2.5, ink: .pen,
                                     color: .black, date: baseDate)
        let marker = TestSupport.stroke(wave(from: CGPoint(x: 60, y: 160), count: 25, height: 2), width: 12, ink: .marker,
                                        color: UIColor(red: 1, green: 0.85, blue: 0, alpha: 1), date: baseDate + 1)
        let pencil = TestSupport.stroke(wave(from: CGPoint(x: 60, y: 240), count: 20, height: 14), width: 1.2, ink: .pencil,
                                        color: UIColor(red: 0.1, green: 0.3, blue: 0.8, alpha: 1), date: baseDate + 2)
        let dot = TestSupport.stroke([CGPoint(x: 300, y: 300)], width: 4, color: .red, date: baseDate + 3)
        return PKDrawing(strokes: [pen, marker, pencil, dot, maskedStroke(date: baseDate + 4)])
    }

    /// A line from x 50 to 350 whose middle third was erased: two visible pieces.
    private func maskedStroke(date: Date) -> PKStroke {
        let points = (0...30).map { CGPoint(x: 50 + CGFloat($0) * 10, y: 380) }
        let base = TestSupport.stroke(points, width: 3, color: .darkGray, date: date)
        let mask = UIBezierPath(rect: CGRect(x: 40, y: 360, width: 110, height: 40))
        mask.append(UIBezierPath(rect: CGRect(x: 250, y: 360, width: 110, height: 40)))
        return PKStroke(ink: base.ink, path: base.path, transform: .identity, mask: mask)
    }

    private func geometries(_ pdf: PDFDocument) -> [PageGeometry] {
        (0..<pdf.pageCount).compactMap { pdf.page(at: $0) }.map { PageGeometry(page: $0) }
    }

    private func render(_ drawings: [PKDrawing], on pdf: PDFDocument, noteID: String = "test-note") throws -> Data {
        let data = try XCTUnwrap(pdf.dataRepresentation())
        return try NoteWriter.render(NoteSnapshot(pdfData: data, drawings: drawings, geometries: geometries(pdf),
                                                  pageCount: pdf.pageCount, noteID: noteID))
    }

    /// The annotations of one page as the file has them, read from disk with CGPDF.
    private func raw(_ data: Data, page: Int = 0, subtype: String? = "Ink") throws -> [RawAnnotation] {
        let url = TestSupport.temporaryFolder().appendingPathComponent("raw.pdf")
        try data.write(to: url)
        return PDFRaw.annotations(inFile: url, pageIndex: page).filter { subtype == nil || $0.subtype == subtype }
    }

    private func isOnGrid(_ value: CGFloat) -> Bool {
        abs(value * 100 - (value * 100).rounded()) < 0.001
    }

    private func assertSameStrokes(_ read: [PKStroke], _ written: [PKStroke], file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(read.count, written.count, file: file, line: line)
        for (a, b) in zip(read, written) {
            XCTAssertEqual(a.ink.inkType, b.ink.inkType, file: file, line: line)
            XCTAssertTrue(RGB(a.ink.color).isClose(to: RGB(b.ink.color), tolerance: 0.01), file: file, line: line)
            XCTAssertEqual(a.path.count, b.path.count, file: file, line: line)
            XCTAssertEqual(a.path.creationDate.timeIntervalSinceReferenceDate,
                           b.path.creationDate.timeIntervalSinceReferenceDate, accuracy: 0.001, file: file, line: line)
            for (p, q) in zip(a.path, b.path) {
                let pa = p.location.applying(a.transform), qb = q.location.applying(b.transform)
                XCTAssertEqual(pa.x, qb.x, accuracy: 0.001, file: file, line: line)
                XCTAssertEqual(pa.y, qb.y, accuracy: 0.001, file: file, line: line)
            }
        }
    }

    /// A copy of `raw` with some keys changed, as a desktop edit would leave them.
    private func edited(_ raw: RawAnnotation, inkList: [[CGPoint]]? = nil, color: [CGFloat]? = nil, opacity: CGFloat? = nil,
                        borderWidth: CGFloat? = nil, highlighter: Bool? = nil, dropPencilKit: Bool = false,
                        dropCheck: Bool = false) -> RawAnnotation {
        RawAnnotation(subtype: raw.subtype, identifier: raw.identifier, rect: raw.rect, flags: raw.flags,
                      color: color ?? raw.color, opacity: opacity ?? raw.opacity, borderWidth: borderWidth ?? raw.borderWidth,
                      inkList: inkList ?? raw.inkList, widths: raw.widths, highlighter: highlighter ?? raw.highlighter,
                      pencilKit: dropPencilKit ? nil : raw.pencilKit, inkCheck: dropCheck ? nil : raw.inkCheck,
                      imageData: raw.imageData, contents: raw.contents, defaultAppearance: raw.defaultAppearance,
                      defaultStyle: raw.defaultStyle, rectDifferences: raw.rectDifferences,
                      appearanceData: raw.appearanceData, appearanceImageCount: raw.appearanceImageCount)
    }

    /// One pen stroke written on a page with `rotation`, read back raw, with its geometry and the stroke written.
    private func writtenStroke(rotation: Int = 0, width: CGFloat = 2, ink: PKInkingTool.InkType = .pen,
                               color: UIColor = .black) throws -> (raw: RawAnnotation, geometry: PageGeometry, stroke: PKStroke) {
        let pdf = TestSupport.pdf(pages: 1, rotation: rotation)
        let stroke = TestSupport.stroke(wave(from: CGPoint(x: 80, y: 120), count: 20), width: width, ink: ink,
                                        color: color, date: baseDate)
        let found = try raw(render([PKDrawing(strokes: [stroke])], on: pdf))
        let page = try XCTUnwrap(pdf.page(at: 0))
        return (try XCTUnwrap(found.first), PageGeometry(page: page), stroke)
    }

    private func pngData(_ size: CGSize = CGSize(width: 40, height: 30)) -> Data {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: size, format: format).pngData { context in
            UIColor(red: 0.8, green: 0.2, blue: 0.3, alpha: 1).setFill()
            context.fill(CGRect(origin: .zero, size: size))
        }
    }

    private func note(pages: Int = 2, in folder: URL, named name: String = "Note") -> URL {
        TestSupport.writeNote(TestSupport.pdf(pages: pages), named: name, in: folder)
    }

    private func pause(_ seconds: Double) async {
        try? await Task.sleep(nanoseconds: UInt64(seconds * 1_000_000_000))
    }

    // MARK: Ink to annotations and back

    @MainActor func testInkRoundTripThroughFile() throws {
        let drawing = sampleDrawing()
        for rotation in [0, 90, 180, 270] {
            let pdf = TestSupport.pdf(pages: 1, rotation: rotation)
            let geometry = try XCTUnwrap(geometries(pdf).first)
            let records = StrokeCodec.records(for: drawing, geometry: geometry)
            XCTAssertEqual(records.count, drawing.strokes.count)
            let data = try render([drawing], on: pdf)
            let inks = try raw(data)
            XCTAssertEqual(inks.count, drawing.strokes.count, "one /Ink per stroke at \(rotation)")
            for (ink, record) in zip(inks, records) {
                let list = try XCTUnwrap(ink.inkList)
                XCTAssertEqual(list.count, record.paths.count)
                for (path, written) in zip(list, record.paths) {
                    XCTAssertEqual(path.count, written.count)
                    for (p, q) in zip(path, written) {
                        XCTAssertTrue(isOnGrid(p.x) && isOnGrid(p.y), "\(p) is not on the 0.01 grid")
                        XCTAssertEqual(p.x, q.x, accuracy: 0.001)
                        XCTAssertEqual(p.y, q.y, accuracy: 0.001)
                    }
                }
                let widths = try XCTUnwrap(ink.widths)
                XCTAssertEqual(widths.map(\.count), list.map(\.count))
                XCTAssertEqual(ink.highlighter, record.highlighter)
                let color = try XCTUnwrap(ink.rgb)
                XCTAssertTrue(color.isClose(to: record.color, tolerance: 0.005))
                XCTAssertEqual(ink.opacity ?? -1, record.opacity, accuracy: 0.005)
                XCTAssertEqual(ink.borderWidth ?? -1, record.maxWidth, accuracy: 0.005)
                XCTAssertNotNil(InkCheck(string: ink.inkCheck ?? ""))
                XCTAssertEqual(ink.identifier, record.identifier)
                XCTAssertFalse(ink.appearanceData?.isEmpty ?? true)
            }
            let reopened = try XCTUnwrap(PDFDocument(data: data))
            let pages = NoteReader.extract(from: reopened)
            XCTAssertEqual(pages.count, 1)
            assertSameStrokes(pages[0].drawing.strokes, drawing.strokes)
        }
    }

    func testInkListIsInPageSpaceOnEveryRotation() throws {
        let points = [CGPoint(x: 100, y: 150), CGPoint(x: 200, y: 250), CGPoint(x: 300, y: 150)]
        let stroke = TestSupport.stroke(points, width: 2, date: baseDate)
        for rotation in [0, 90, 180, 270] {
            let pdf = TestSupport.pdf(pages: 1, rotation: rotation)
            let geometry = try XCTUnwrap(geometries(pdf).first)
            let ink = try XCTUnwrap(try raw(render([PKDrawing(strokes: [stroke])], on: pdf)).first)
            let list = try XCTUnwrap(ink.inkList?.first)
            // The polyline runs through every control point (a straight path per segment is not guaranteed, its ends are).
            let first = geometry.toPDF(try XCTUnwrap(StrokeCodec.polylines(of: stroke).first?.points.first))
            XCTAssertEqual(list[0].x, first.x, accuracy: 0.011, "rotation \(rotation)")
            XCTAssertEqual(list[0].y, first.y, accuracy: 0.011, "rotation \(rotation)")
            // Every written point lies on the page, in its user space.
            for p in list {
                XCTAssertTrue(geometry.box.insetBy(dx: -1, dy: -1).contains(p), "\(p) off the page at \(rotation)")
            }
            XCTAssertTrue(ink.rect.contains(list[0]), "the /Rect holds the ink at \(rotation)")
        }
    }

    @MainActor func testCropBoxOffset() throws {
        let pdf = TestSupport.pdf(pages: 1)
        let page = try XCTUnwrap(pdf.page(at: 0))
        page.setBounds(CGRect(x: 50, y: 60, width: 400, height: 500), for: .cropBox)
        let geometry = PageGeometry(page: page)
        XCTAssertEqual(geometry.box.minX, 50, accuracy: 0.01)
        let stroke = TestSupport.stroke([CGPoint(x: 10, y: 20), CGPoint(x: 110, y: 60), CGPoint(x: 210, y: 20)], date: baseDate)
        let data = try render([PKDrawing(strokes: [stroke])], on: pdf)
        let ink = try XCTUnwrap(try raw(data).first)
        let first = try XCTUnwrap(ink.inkList?.first?.first)
        XCTAssertEqual(first.x, 60, accuracy: 0.011)    // 10 + left
        XCTAssertEqual(first.y, 540, accuracy: 0.011)   // top (560) - 20
        let reopened = try XCTUnwrap(PDFDocument(data: data))
        assertSameStrokes(NoteReader.extract(from: reopened)[0].drawing.strokes, [stroke])
    }

    func testWidthsAreNestedArrays() throws {
        let inks = try raw(render([sampleDrawing()], on: TestSupport.pdf(pages: 1)))
        XCTAssertFalse(inks.isEmpty)
        for ink in inks {
            let widths = try XCTUnwrap(ink.widths, "/ZenithWidths is not an array of arrays of numbers")
            let list = try XCTUnwrap(ink.inkList)
            XCTAssertEqual(widths.count, list.count)
            XCTAssertEqual(widths.map(\.count), list.map(\.count))
            XCTAssertTrue(widths.joined().allSatisfy { $0 > 0 && isOnGrid($0) })
        }
    }

    func testHighlighterFlagOnlyOnMarker() throws {
        let drawing = sampleDrawing()
        let inks = try raw(render([drawing], on: TestSupport.pdf(pages: 1)))
        XCTAssertEqual(inks.map(\.highlighter), drawing.strokes.map { $0.ink.inkType == .marker })
        XCTAssertEqual(inks.filter(\.highlighter).count, 1)
    }

    func testOpacityWritten() throws {
        let faint = TestSupport.stroke(wave(from: CGPoint(x: 50, y: 50), count: 10), color: UIColor(red: 0, green: 0, blue: 1, alpha: 0.3),
                                       date: baseDate)
        let solid = TestSupport.stroke(wave(from: CGPoint(x: 50, y: 150), count: 10), color: UIColor(red: 0, green: 0, blue: 1, alpha: 1),
                                       date: baseDate + 1)
        let inks = try raw(render([PKDrawing(strokes: [faint, solid])], on: TestSupport.pdf(pages: 1)))
        XCTAssertEqual(inks.count, 2)
        XCTAssertEqual(try XCTUnwrap(inks.first?.opacity), 0.3, accuracy: 0.005)
        XCTAssertEqual(try XCTUnwrap(inks.last?.opacity), 1.0, accuracy: 0.005)
    }

    func testStableIdentifiers() throws {
        let drawing = sampleDrawing()
        let pdf = TestSupport.pdf(pages: 1)
        let first = try raw(render([drawing], on: pdf)).compactMap(\.identifier)
        let second = try raw(render([drawing], on: pdf)).compactMap(\.identifier)
        XCTAssertEqual(first.count, drawing.strokes.count)
        XCTAssertEqual(first, second)
        XCTAssertEqual(Set(first).count, first.count)
        XCTAssertTrue(first.allSatisfy { $0.hasPrefix("zenith-") })
        // A copy of a stroke in place still gets an id of its own.
        let doubled = PKDrawing(strokes: [drawing.strokes[0], drawing.strokes[0]])
        let ids = StrokeCodec.records(for: doubled, geometry: try XCTUnwrap(geometries(pdf).first)).map(\.identifier)
        XCTAssertEqual(Set(ids).count, 2)
    }

    func testMaskedStrokeWritesVisiblePieces() throws {
        let stroke = maskedStroke(date: baseDate)
        XCTAssertEqual(stroke.maskedPathRanges.count, 2)
        let pieces = StrokeCodec.polylines(of: stroke)
        XCTAssertEqual(pieces.count, 2)
        let firstXs = pieces[0].points.map(\.x), secondXs = pieces[1].points.map(\.x)
        XCTAssertLessThanOrEqual(firstXs.max() ?? 999, 155)
        XCTAssertGreaterThanOrEqual(secondXs.min() ?? 0, 245)
        let ink = try XCTUnwrap(try raw(render([PKDrawing(strokes: [stroke])], on: TestSupport.pdf(pages: 1))).first)
        XCTAssertEqual(ink.inkList?.count, 2)
        XCTAssertEqual(ink.widths?.count, 2)
    }

    func testDotBecomesTwoPoints() throws {
        let dot = TestSupport.stroke([CGPoint(x: 200, y: 300)], width: 5, date: baseDate)
        let geometry = PageGeometry(box: CGRect(x: 0, y: 0, width: 595, height: 842), rotation: 0)
        let record = try XCTUnwrap(StrokeCodec.record(for: dot, geometry: geometry))
        XCTAssertEqual(record.paths.count, 1)
        XCTAssertEqual(record.paths[0].count, 2)
        XCTAssertEqual(record.paths[0][1].x - record.paths[0][0].x, 0.01, accuracy: 0.0001)
        XCTAssertEqual(record.paths[0][0].y, 542, accuracy: 0.001)
        XCTAssertEqual(record.widths[0].count, 2)
    }

    func testOneAnnotationPerStroke() throws {
        let strokes = (0..<5).map { i in
            TestSupport.stroke(wave(from: CGPoint(x: 50, y: 60 + CGFloat(i) * 70), count: 12), date: baseDate + Double(i))
        }
        let inks = try raw(render([PKDrawing(strokes: strokes)], on: TestSupport.pdf(pages: 1)))
        XCTAssertEqual(inks.count, 5)
        XCTAssertTrue(inks.allSatisfy { $0.inkList?.count == 1 })
    }

    func testThousandStrokesSaveFast() throws {
        let folder = TestSupport.temporaryFolder()
        let url = note(pages: 1, in: folder)
        let before = try Data(contentsOf: url).count
        let strokes = (0..<1000).map { i -> PKStroke in
            let row = CGFloat(i / 20), column = CGFloat(i % 20)
            return TestSupport.stroke(wave(from: CGPoint(x: 20 + column * 28, y: 20 + row * 16), count: 15, step: 1.6, height: 4),
                                      width: 1.8, date: baseDate + Double(i))
        }
        let pdf = try XCTUnwrap(PDFDocument(url: url))
        let snapshot = NoteSnapshot(pdfData: try XCTUnwrap(pdf.dataRepresentation()), drawings: [PKDrawing(strokes: strokes)],
                                    geometries: geometries(pdf), pageCount: 1, noteID: "volume")
        // One small save first, so the timing leaves out the one-time setup of PencilKit's encoder and PDFKit's writer.
        _ = try NoteWriter.render(NoteSnapshot(pdfData: snapshot.pdfData, drawings: [PKDrawing(strokes: Array(strokes.prefix(20)))],
                                               geometries: snapshot.geometries, pageCount: 1, noteID: "warm-up"))
        let start = Date()
        _ = try NoteWriter.write(snapshot, to: url, known: nil, keepPrevious: nil)
        let elapsed = Date().timeIntervalSince(start)
        XCTAssertLessThan(elapsed, 2.0, "1,000 strokes took \(elapsed) s")
        let recordsStart = Date()
        _ = StrokeCodec.records(for: PKDrawing(strokes: strokes), geometry: snapshot.geometries[0])
        let recordsTime = Date().timeIntervalSince(recordsStart)
        let records = StrokeCodec.records(for: PKDrawing(strokes: strokes), geometry: snapshot.geometries[0])
        let copy = try XCTUnwrap(PDFDocument(data: snapshot.pdfData))
        let page = try XCTUnwrap(copy.page(at: 0))
        let addStart = Date()
        for record in records { page.addAnnotation(InkAnnotation(record: record)) }
        let addTime = Date().timeIntervalSince(addStart)
        let writeStart = Date()
        _ = copy.dataRepresentation()
        print("1,000 strokes: \(elapsed) s to save; records \(recordsTime) s, annotations \(addTime) s, " +
              "PDFKit writing \(Date().timeIntervalSince(writeStart)) s")
        let grown = try Data(contentsOf: url).count - before
        XCTAssertLessThan(grown, 3_000_000, "1,000 strokes added \(grown) bytes")
        XCTAssertEqual(PDFRaw.annotations(inFile: url, pageIndex: 0).filter { $0.subtype == "Ink" }.count, 1000)
    }

    func testAppearanceStreamWritten() throws {
        let inks = try raw(render([sampleDrawing()], on: TestSupport.pdf(pages: 1)))
        for ink in inks {
            let content = try XCTUnwrap(ink.appearanceData, "no /AP /N")
            XCTAssertFalse(content.isEmpty)
        }
    }

    func testInkCheckWrittenAndParsed() throws {
        let (ink, geometry, stroke) = try writtenStroke(rotation: 90, width: 3, color: UIColor(red: 0.2, green: 0.4, blue: 0.6, alpha: 0.5))
        let record = try XCTUnwrap(StrokeCodec.record(for: stroke, geometry: geometry))
        let check = try XCTUnwrap(InkCheck(string: try XCTUnwrap(ink.inkCheck)))
        XCTAssertEqual(check.shape, record.check.shape)
        XCTAssertEqual(check.shape, InkCheck.shapeHash(try XCTUnwrap(ink.inkList)))
        XCTAssertEqual(check.origin.x, record.paths[0][0].x, accuracy: 0.001)
        XCTAssertEqual(check.origin.y, record.paths[0][0].y, accuracy: 0.001)
        XCTAssertEqual(check.rotation, 90)
        XCTAssertEqual(check.box.width, geometry.box.width, accuracy: 0.006)
        XCTAssertTrue(check.color.isClose(to: RGB(r: 0.2, g: 0.4, b: 0.6), tolerance: 0.006))
        XCTAssertEqual(check.opacity, 0.5, accuracy: 0.006)
        XCTAssertFalse(check.highlighter)
        XCTAssertEqual(check.width, record.maxWidth, accuracy: 0.006)
        XCTAssertEqual(InkCheck(string: check.string)?.string, check.string)
        XCTAssertNil(InkCheck(string: "2" + check.string.dropFirst()))
        XCTAssertNil(InkCheck(string: "1;abc;1 2"))
        XCTAssertNil(InkCheck(string: ""))
    }

    func testShapeHashIgnoresTranslationOnly() {
        let paths: [[CGPoint]] = [[CGPoint(x: 10.25, y: 20.5), CGPoint(x: 30.75, y: 40), CGPoint(x: 50.01, y: 25.33)],
                                  [CGPoint(x: 60, y: 70)]]
        let moved = paths.map { $0.map { CGPoint(x: $0.x + 12.37, y: $0.y - 7.11) } }
        XCTAssertEqual(InkCheck.shapeHash(paths), InkCheck.shapeHash(moved))
        var reshaped = paths
        reshaped[0][1].x += 0.02
        XCTAssertNotEqual(InkCheck.shapeHash(paths), InkCheck.shapeHash(reshaped))
        XCTAssertNotEqual(InkCheck.shapeHash(paths), InkCheck.shapeHash([paths[0]]))
        XCTAssertNotEqual(InkCheck.shapeHash(paths), InkCheck.shapeHash([paths[0] + paths[1]]))
    }

    // MARK: Desktop edits, simulated

    func testTranslatedInkKeepsPencilKitStrokes() throws {
        for rotation in [0, 90] {
            let (ink, geometry, stroke) = try writtenStroke(rotation: rotation)
            let list = try XCTUnwrap(ink.inkList)
            let moved = edited(ink, inkList: list.map { $0.map { CGPoint(x: $0.x + 10, y: $0.y - 5) } })
            let strokes = StrokeCodec.strokes(from: moved, geometry: geometry)
            XCTAssertEqual(strokes.count, 1)
            let read = try XCTUnwrap(strokes.first)
            XCTAssertEqual(read.path.count, stroke.path.count)
            XCTAssertEqual(read.path.creationDate.timeIntervalSinceReferenceDate,
                           stroke.path.creationDate.timeIntervalSinceReferenceDate, accuracy: 0.001)
            XCTAssertEqual(read.ink.inkType, stroke.ink.inkType)
            let a = geometry.toDisplay(CGPoint(x: 100, y: 100)), b = geometry.toDisplay(CGPoint(x: 110, y: 95))
            let expected = CGPoint(x: b.x - a.x, y: b.y - a.y)
            let shown = read.path[0].location.applying(read.transform)
            XCTAssertEqual(shown.x - stroke.path[0].location.x, expected.x, accuracy: 0.001, "rotation \(rotation)")
            XCTAssertEqual(shown.y - stroke.path[0].location.y, expected.y, accuracy: 0.001, "rotation \(rotation)")
        }
    }

    func testRecolouredInkKeepsStrokes() throws {
        let (ink, geometry, stroke) = try writtenStroke()
        let read = StrokeCodec.strokes(from: edited(ink, color: [0.8, 0.1, 0.1], opacity: 0.6), geometry: geometry)
        let first = try XCTUnwrap(read.first)
        XCTAssertEqual(read.count, 1)
        XCTAssertEqual(first.path.creationDate.timeIntervalSinceReferenceDate,
                       stroke.path.creationDate.timeIntervalSinceReferenceDate, accuracy: 0.001)
        XCTAssertEqual(first.path.count, stroke.path.count)
        let style = StrokeCodec.style(of: first.ink)
        XCTAssertTrue(style.color.isClose(to: RGB(r: 0.8, g: 0.1, b: 0.1), tolerance: 0.01))
        XCTAssertEqual(style.opacity, 0.6, accuracy: 0.01)
        XCTAssertEqual(first.ink.inkType, .pen)
        // Unchanged keys read back as the stored stroke, untouched.
        let same = try XCTUnwrap(StrokeCodec.strokes(from: ink, geometry: geometry).first)
        XCTAssertEqual(same.transform, stroke.transform)
        XCTAssertTrue(RGB(same.ink.color).isClose(to: RGB(stroke.ink.color), tolerance: 0.01))
    }

    func testWidthChangeScalesStrokes() throws {
        let (ink, geometry, stroke) = try writtenStroke(width: 2)
        let width = try XCTUnwrap(ink.borderWidth)
        let read = try XCTUnwrap(StrokeCodec.strokes(from: edited(ink, borderWidth: width * 2), geometry: geometry).first)
        XCTAssertEqual(read.path.count, stroke.path.count)
        for (p, q) in zip(read.path, stroke.path) {
            XCTAssertEqual(p.size.width, q.size.width * 2, accuracy: 0.01)
        }
        XCTAssertEqual(read.path.creationDate.timeIntervalSinceReferenceDate,
                       stroke.path.creationDate.timeIntervalSinceReferenceDate, accuracy: 0.001)
    }

    func testHighlighterToggleRestyles() throws {
        let (ink, geometry, stroke) = try writtenStroke()
        let marked = try XCTUnwrap(StrokeCodec.strokes(from: edited(ink, highlighter: true), geometry: geometry).first)
        XCTAssertEqual(marked.ink.inkType, .marker)
        XCTAssertEqual(marked.path.creationDate.timeIntervalSinceReferenceDate,
                       stroke.path.creationDate.timeIntervalSinceReferenceDate, accuracy: 0.001)
        let (markerInk, markerGeometry, _) = try writtenStroke(width: 10, ink: .marker, color: .yellow)
        XCTAssertTrue(markerInk.highlighter)
        let plain = try XCTUnwrap(StrokeCodec.strokes(from: edited(markerInk, highlighter: false), geometry: markerGeometry).first)
        XCTAssertEqual(plain.ink.inkType, .pen)
    }

    func testEditedInkIsRebuilt() throws {
        let (ink, geometry, _) = try writtenStroke()
        var list = try XCTUnwrap(ink.inkList)
        list[0][2].y += 3
        let read = StrokeCodec.strokes(from: edited(ink, inkList: list), geometry: geometry)
        XCTAssertEqual(read.count, 1)
        let rebuilt = try XCTUnwrap(read.first)
        XCTAssertEqual(rebuilt.path.creationDate, .distantPast)
        XCTAssertEqual(rebuilt.path.count, list[0].count * 3)
        let moved = geometry.toDisplay(list[0][2])
        XCTAssertEqual(rebuilt.path[7].location.x, moved.x, accuracy: 0.001)
        XCTAssertEqual(rebuilt.path[7].location.y, moved.y, accuracy: 0.001)
    }

    func testMissingKeyIsRebuilt() throws {
        let (ink, geometry, _) = try writtenStroke()
        for changed in [edited(ink, dropPencilKit: true), edited(ink, dropCheck: true)] {
            let read = try XCTUnwrap(StrokeCodec.strokes(from: changed, geometry: geometry).first)
            XCTAssertEqual(read.path.creationDate, .distantPast)
            XCTAssertEqual(read.path.count, (ink.inkList?.first?.count ?? 0) * 3)
        }
    }

    @MainActor func testRotatedAfterWriteIsRebuilt() throws {
        let pdf = TestSupport.pdf(pages: 1)
        let stroke = TestSupport.stroke(wave(from: CGPoint(x: 80, y: 120), count: 20), date: baseDate)
        let data = try render([PKDrawing(strokes: [stroke])], on: pdf)
        let reopened = try XCTUnwrap(PDFDocument(data: data))
        let page = try XCTUnwrap(reopened.page(at: 0))
        let list = try XCTUnwrap(PDFRaw.annotations(of: page).first?.inkList?.first)
        page.rotation = 90
        let geometry = PageGeometry(page: page)
        let read = try XCTUnwrap(NoteReader.extract(from: reopened).first?.drawing.strokes.first)
        XCTAssertEqual(read.path.creationDate, .distantPast)
        XCTAssertEqual(read.path.count, list.count * 3)
        for (i, point) in list.enumerated() {
            let shown = geometry.toDisplay(point)
            XCTAssertEqual(read.path[i * 3 + 1].location.x, shown.x, accuracy: 0.001)
            XCTAssertEqual(read.path[i * 3 + 1].location.y, shown.y, accuracy: 0.001)
        }
    }

    func testRebuildTriplesPoints() {
        let geometry = PageGeometry(box: CGRect(x: 0, y: 0, width: 595, height: 842), rotation: 0)
        let path = [CGPoint(x: 10, y: 800), CGPoint(x: 50, y: 760), CGPoint(x: 90, y: 800)]
        let strokes = StrokeCodec.rebuild(paths: [path, [CGPoint(x: 5, y: 5)]], widths: [[1, 2, 3], [4]], width: 9,
                                          color: RGB(r: 0, g: 0, b: 1), opacity: 0.5, highlighter: false, geometry: geometry)
        XCTAssertEqual(strokes.count, 2)
        XCTAssertEqual(strokes[0].path.count, 9)
        for i in 0..<9 {
            let expected = geometry.toDisplay(path[i / 3])
            XCTAssertEqual(strokes[0].path[i].location.x, expected.x, accuracy: 0.0001)
            XCTAssertEqual(strokes[0].path[i].location.y, expected.y, accuracy: 0.0001)
            XCTAssertEqual(strokes[0].path[i].size.width, CGFloat(i / 3 + 1), accuracy: 0.0001)
        }
        XCTAssertEqual(strokes[1].path.count, 3)
        XCTAssertEqual(strokes[0].ink.inkType, .pen)
        XCTAssertEqual(StrokeCodec.style(of: strokes[0].ink).opacity, 0.5, accuracy: 0.01)
        // Widths that do not fit every point give every point the annotation's width.
        let plain = StrokeCodec.rebuild(paths: [path], widths: [[1, 2]], width: 9, color: .black, opacity: 1,
                                        highlighter: true, geometry: geometry)
        XCTAssertTrue(plain[0].path.allSatisfy { abs($0.size.width - 9) < 0.0001 })
        XCTAssertEqual(plain[0].ink.inkType, .marker)
    }

    func testRebuiltInkSavesAsSamePolyline() throws {
        let geometry = PageGeometry(box: CGRect(x: 0, y: 0, width: 595, height: 842), rotation: 0)
        // A zigzag with sharp corners, short and long segments, as desktop ink has them.
        let display: [CGPoint] = [CGPoint(x: 40, y: 40), CGPoint(x: 41, y: 60), CGPoint(x: 90, y: 42), CGPoint(x: 92.5, y: 120),
                                  CGPoint(x: 140, y: 118), CGPoint(x: 141, y: 119), CGPoint(x: 200, y: 40)]
        let pdfPoints = display.map { geometry.toPDF($0) }
        let strokes = StrokeCodec.rebuild(paths: [pdfPoints], widths: nil, width: 2, color: .black, opacity: 1,
                                          highlighter: false, geometry: geometry)
        let records = StrokeCodec.records(for: PKDrawing(strokes: strokes), geometry: geometry)
        let written = try XCTUnwrap(records.first?.paths.first).map { geometry.toDisplay($0) }
        func distance(_ p: CGPoint, to line: [CGPoint]) -> CGFloat {
            zip(line, line.dropFirst()).map { PolylineMath.segmentDistance(p, $0, $1) }.min() ?? .infinity
        }
        for p in display { XCTAssertLessThanOrEqual(distance(p, to: written), 0.15, "corner \(p) lost") }
        for p in written { XCTAssertLessThanOrEqual(distance(p, to: display), 0.15, "\(p) off the desktop polyline") }
    }

    // MARK: Text boxes

    private func writtenTextBox(_ style: TextBoxStyle) throws -> RawAnnotation {
        let pdf = TestSupport.pdf(pages: 1)
        let page = try XCTUnwrap(pdf.page(at: 0))
        let bounds = TextBoxCodec.fittedBounds(text: "Newton's laws", style: style, topLeft: CGPoint(x: 72, y: 700), width: 200)
        page.addAnnotation(TextBoxCodec.makeAnnotation(text: "Newton's laws", style: style, bounds: bounds))
        return try XCTUnwrap(try raw(try XCTUnwrap(pdf.dataRepresentation()), subtype: "FreeText").first)
    }

    func testTextBoxHasNoFill() throws {
        let box = try writtenTextBox(TextBoxStyle())
        XCTAssertTrue(box.color == nil || box.color?.isEmpty == true, "/C is \(String(describing: box.color))")
        XCTAssertEqual(box.contents, "Newton's laws")
    }

    func testTextBoxFontReadable() throws {
        var style = TextBoxStyle()
        style.bold = true
        let box = try writtenTextBox(style)
        let appearance = try XCTUnwrap(box.defaultAppearance)
        XCTAssertTrue(appearance.contains("Helvetica-Bold"), "/DA is \(appearance)")
    }

    func testTextBoxWritesDesktopStyle() throws {
        var style = TextBoxStyle()
        style.bold = true
        XCTAssertEqual(TextBoxCodec.styleString(style), "font: Helvetica,sans-serif 14pt; text-align:left; color:#000000; font-weight:bold")
        var times = TextBoxStyle()
        times.font = .times
        times.size = 18.5
        times.italic = true
        times.alignment = 1
        times.color = RGB(r: 1, g: 0.5, b: 0)
        XCTAssertEqual(TextBoxCodec.styleString(times), "font: Times,serif 18.5pt; text-align:center; color:#FF8000; font-style:italic")
        let box = try writtenTextBox(style)
        XCTAssertEqual(box.defaultStyle, TextBoxCodec.styleString(style))
        XCTAssertNil(box.rectDifferences)

        // Read back through PDFKit, /DS wins over the font name, as on the desktop.
        let pdf = TestSupport.pdf(pages: 1)
        let page = try XCTUnwrap(pdf.page(at: 0))
        page.addAnnotation(TextBoxCodec.makeAnnotation(text: "x", style: times, bounds: CGRect(x: 72, y: 600, width: 200, height: 40)))
        let reopened = try XCTUnwrap(PDFDocument(data: try XCTUnwrap(pdf.dataRepresentation())))
        let annotation = try XCTUnwrap(reopened.page(at: 0)?.annotations.first(where: TextBoxCodec.isTextBox))
        let read = TextBoxCodec.style(of: annotation)
        XCTAssertEqual(read.font, .times)
        XCTAssertTrue(read.italic)
        XCTAssertFalse(read.bold)
        XCTAssertEqual(read.size, 18.5, accuracy: 0.01)
        XCTAssertEqual(read.alignment, 1)
    }

    // MARK: Pictures

    @MainActor func testImageAnnotationRoundTrip() throws {
        let data = pngData()
        let pdf = TestSupport.pdf(pages: 1)
        let page = try XCTUnwrap(pdf.page(at: 0))
        let bounds = CGRect(x: 100, y: 500, width: 200, height: 150)
        page.addAnnotation(ImageAnnotation(bounds: bounds, imageData: data))
        let written = try XCTUnwrap(pdf.dataRepresentation())
        let stamp = try XCTUnwrap(try raw(written, subtype: "Stamp").first)
        XCTAssertEqual(stamp.imageData.flatMap { Data(base64Encoded: $0) }, data)
        let reopened = try XCTUnwrap(PDFDocument(data: written))
        _ = NoteReader.extract(from: reopened)
        let pictures = (reopened.page(at: 0)?.annotations ?? []).compactMap { $0 as? ImageAnnotation }
        XCTAssertEqual(pictures.count, 1)
        let picture = try XCTUnwrap(pictures.first)
        XCTAssertEqual(picture.imageData, data)
        XCTAssertNotNil(picture.image)
        XCTAssertEqual(picture.bounds.minX, bounds.minX, accuracy: 0.5)
        XCTAssertEqual(picture.bounds.height, bounds.height, accuracy: 0.5)
        XCTAssertNotNil(ImageAnnotation.preparedData(for: try XCTUnwrap(UIImage(data: data))))
    }

    func testPictureAppearanceIsImage() throws {
        let pdf = TestSupport.pdf(pages: 1)
        let page = try XCTUnwrap(pdf.page(at: 0))
        page.addAnnotation(ImageAnnotation(bounds: CGRect(x: 100, y: 500, width: 200, height: 150), imageData: pngData()))
        let stamp = try XCTUnwrap(try raw(try XCTUnwrap(pdf.dataRepresentation()), subtype: "Stamp").first)
        let content = String(decoding: try XCTUnwrap(stamp.appearanceData, "no /AP /N"), as: UTF8.self)
        XCTAssertTrue(content.contains("Do"), "the appearance draws no XObject: \(content.prefix(200))")
        XCTAssertEqual(stamp.appearanceImageCount, 1)
    }

    func testSubclassesSurviveCopy() throws {
        let data = pngData()
        let pdf = TestSupport.pdf(pages: 1)
        let page = try XCTUnwrap(pdf.page(at: 0))
        let geometry = PageGeometry(page: page)
        let record = try XCTUnwrap(StrokeCodec.record(for: TestSupport.stroke(wave(from: CGPoint(x: 50, y: 50), count: 8)),
                                                      geometry: geometry))
        page.addAnnotation(InkAnnotation(record: record))
        page.addAnnotation(ImageAnnotation(bounds: CGRect(x: 100, y: 500, width: 80, height: 60), imageData: data))
        XCTAssertNotNil(page.copy() as? PDFPage)
        for annotation in page.annotations {
            XCTAssertNotNil(annotation.copy() as? PDFAnnotation)
        }
        let reopened = try XCTUnwrap(PDFDocument(data: try XCTUnwrap(pdf.dataRepresentation())))
        let stamp = try XCTUnwrap(try raw(try XCTUnwrap(reopened.dataRepresentation()), subtype: "Stamp").first)
        XCTAssertEqual(stamp.imageData.flatMap { Data(base64Encoded: $0) }, data)
        let decoded = ImageAnnotation(bounds: .zero, forType: .stamp, withProperties: nil)
        XCTAssertTrue(decoded.imageData.isEmpty)
    }

    // MARK: The open note

    @MainActor func testNoteIDWrittenAndKept() async throws {
        let folder = TestSupport.temporaryFolder()
        let url = note(in: folder)
        XCTAssertNil(PDFRaw.documentInfo(inFile: url)["ZenithNoteID"])
        let document = try await NoteDocument.open(url, libraryRoot: folder)
        let id = document.noteID
        XCTAssertFalse(id.isEmpty)
        document.setDrawing(PKDrawing(strokes: [TestSupport.stroke(wave(from: CGPoint(x: 60, y: 60), count: 10))]),
                            forPageID: document.pageID(at: 0), undoable: true)
        await document.save()
        XCTAssertNil(document.lastError)
        XCTAssertEqual(PDFRaw.documentInfo(inFile: url)["ZenithNoteID"], id)
        XCTAssertEqual(MetadataStore.load(forNote: url, libraryRoot: folder).noteID, id)
        await document.close()
        let reopened = try await NoteDocument.open(url, libraryRoot: folder)
        XCTAssertEqual(reopened.noteID, id)
        XCTAssertEqual(reopened.pages[0].drawing.strokes.count, 1)
    }

    @MainActor func testNoteReaderRemovesInkFromLiveDocument() throws {
        let drawing = sampleDrawing()
        let pdf = TestSupport.pdf(pages: 2)
        let data = try render([drawing, PKDrawing()], on: pdf)
        let live = try XCTUnwrap(PDFDocument(data: data))
        XCTAssertEqual(live.page(at: 0)?.annotations.filter { $0.type == "Ink" }.count, drawing.strokes.count)
        let pages = NoteReader.extract(from: live)
        XCTAssertEqual(pages.count, 2)
        XCTAssertEqual(pages[0].drawing.strokes.count, drawing.strokes.count)
        XCTAssertTrue(pages[1].drawing.strokes.isEmpty)
        XCTAssertEqual(live.page(at: 0)?.annotations.filter { $0.type == "Ink" }.count, 0)
        XCTAssertEqual(live.page(at: 0)?.annotations.filter { $0.type == "Popup" }.count, 0)
    }

    @MainActor func testSaveIsDebouncedAndSerial() async throws {
        let folder = TestSupport.temporaryFolder()
        let url = note(in: folder)
        let document = try await NoteDocument.open(url, libraryRoot: folder)
        var saves = 0
        document.onSaved = { saves += 1 }
        let page = document.pageID(at: 0)
        func addStroke(_ i: Int) {
            var drawing = document.drawing(forPageID: page)
            drawing.strokes.append(TestSupport.stroke(wave(from: CGPoint(x: 40, y: 40 + CGFloat(i) * 30), count: 8),
                                                      date: baseDate + Double(i)))
            document.setDrawing(drawing, forPageID: page, undoable: true)
        }

        // Debounced: changes 1 s apart push the save to 5 s after the last one.
        addStroke(0)
        await pause(1)
        addStroke(1)
        await pause(4.4)
        XCTAssertEqual(saves, 0, "saved before 5 s of quiet")
        await pause(1.6)
        XCTAssertEqual(saves, 1)
        XCTAssertFalse(document.isDirty)

        // Never sooner than 10 s after the previous save began.
        addStroke(2)
        await pause(6)
        XCTAssertEqual(saves, 1, "saved sooner than 10 s after the previous save")
        await pause(4.5)
        XCTAssertEqual(saves, 2)

        // Serial: a save asked for while one runs waits for it, then saves the newer ink.
        addStroke(3)
        let first = Task { await document.save() }
        var spins = 0
        while !document.isSaving && spins < 200 {
            await Task.yield()
            spins += 1
        }
        addStroke(4)
        await document.save()
        await first.value
        while document.isSaving { await pause(0.05) }
        XCTAssertEqual(saves, 4)
        XCTAssertFalse(document.isDirty)
        XCTAssertEqual(PDFRaw.annotations(inFile: url, pageIndex: 0).filter { $0.subtype == "Ink" }.count, 5)
    }

    @MainActor func testInkOnlySaveReusesBaseData() async throws {
        let folder = TestSupport.temporaryFolder()
        let url = note(in: folder)
        let document = try await NoteDocument.open(url, libraryRoot: folder)
        let page = document.pageID(at: 0)
        document.setDrawing(PKDrawing(strokes: [TestSupport.stroke(wave(from: CGPoint(x: 60, y: 60), count: 10))]),
                            forPageID: page, undoable: true)
        await document.save()
        // A change to the live PDF that nobody announced: an ink-only save must not serialise the live PDF again.
        let livePage = try XCTUnwrap(document.pdf.page(at: 0))
        livePage.addAnnotation(TextBoxCodec.makeAnnotation(text: "unannounced", style: TextBoxStyle(),
                                                           bounds: CGRect(x: 72, y: 600, width: 200, height: 30)))
        var drawing = document.drawing(forPageID: page)
        drawing.strokes.append(TestSupport.stroke(wave(from: CGPoint(x: 60, y: 160), count: 10), date: baseDate + 5))
        document.setDrawing(drawing, forPageID: page, undoable: true)
        await document.save()
        var annotations = PDFRaw.annotations(inFile: url, pageIndex: 0)
        XCTAssertEqual(annotations.filter { $0.subtype == "Ink" }.count, 2)
        XCTAssertEqual(annotations.filter { $0.subtype == "FreeText" }.count, 0)
        document.markChanged()
        await document.save()
        annotations = PDFRaw.annotations(inFile: url, pageIndex: 0)
        XCTAssertEqual(annotations.filter { $0.subtype == "FreeText" }.count, 1)
        XCTAssertEqual(annotations.filter { $0.subtype == "Ink" }.count, 2)
    }

    @MainActor func testSaveHoldsBackgroundTask() async throws {
        let folder = TestSupport.temporaryFolder()
        let url = note(in: folder)
        let document = try await NoteDocument.open(url, libraryRoot: folder)
        var begun: [String] = []
        var ended = 0
        var endedBeforeSaved = -1
        document.beginBackgroundTask = { name in
            begun.append(name)
            return { ended += 1 }
        }
        document.onSaved = { endedBeforeSaved = ended }
        await document.save()
        XCTAssertTrue(begun.isEmpty, "a clean note does not save")
        document.markChanged()
        await document.save()
        XCTAssertEqual(begun, ["save"])
        XCTAssertEqual(endedBeforeSaved, 0, "the task ended before the save did")
        XCTAssertEqual(ended, 1)
    }

    func testFailedRenderKeepsOriginal() throws {
        let folder = TestSupport.temporaryFolder()
        let url = note(pages: 1, in: folder)
        let original = try Data(contentsOf: url)
        let pdf = try XCTUnwrap(PDFDocument(url: url))
        let wrong = NoteSnapshot(pdfData: original, drawings: [PKDrawing(), PKDrawing()], geometries: geometries(pdf),
                                 pageCount: 2, noteID: "x")
        XCTAssertThrowsError(try NoteWriter.write(wrong, to: url, known: nil, keepPrevious: nil)) { error in
            guard case ZenithError.cannotSave(let failed, _) = error else { return XCTFail("\(error)") }
            XCTAssertEqual(failed, url)
        }
        let garbage = NoteSnapshot(pdfData: Data("not a pdf".utf8), drawings: [PKDrawing()], geometries: geometries(pdf),
                                   pageCount: 1, noteID: "x")
        XCTAssertThrowsError(try NoteWriter.write(garbage, to: url, known: nil, keepPrevious: nil))
        XCTAssertEqual(try Data(contentsOf: url), original)
    }

    private func snapshot(of url: URL, strokes: Int = 1) throws -> NoteSnapshot {
        let pdf = try XCTUnwrap(PDFDocument(url: url))
        let drawing = PKDrawing(strokes: (0..<strokes).map {
            TestSupport.stroke(wave(from: CGPoint(x: 50, y: 50 + CGFloat($0) * 40), count: 8), date: baseDate + Double($0))
        })
        return NoteSnapshot(pdfData: try XCTUnwrap(pdf.dataRepresentation()),
                            drawings: [drawing] + Array(repeating: PKDrawing(), count: max(pdf.pageCount - 1, 0)),
                            geometries: geometries(pdf), pageCount: pdf.pageCount, noteID: "conflict")
    }

    private func known(_ url: URL) throws -> SavedFile {
        SavedFile(url: url, modified: try XCTUnwrap(FileStore.modificationDate(url)), hash: FileStore.sha256(try Data(contentsOf: url)))
    }

    func testConflictWritesCopy() throws {
        let folder = TestSupport.temporaryFolder()
        let url = note(in: folder)
        let opened = try known(url)
        // Another device saved a different note in its place.
        let other = TestSupport.pdf(pages: 3)
        XCTAssertTrue(other.write(to: url))
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(60)], ofItemAtPath: url.path)
        let theirs = try Data(contentsOf: url)
        let result = try NoteWriter.write(try snapshot(of: url), to: url, known: opened, keepPrevious: nil)
        XCTAssertEqual(result.conflictWith, url)
        XCTAssertEqual(result.saved.url.lastPathComponent, "Note (iPad).pdf")
        XCTAssertEqual(try Data(contentsOf: url), theirs)
        XCTAssertTrue(FileStore.exists(result.saved.url))
        XCTAssertEqual(result.saved.hash, FileStore.sha256(try Data(contentsOf: result.saved.url)))
    }

    func testDateChangeWithSameContentIsNoConflict() throws {
        let folder = TestSupport.temporaryFolder()
        let url = note(in: folder)
        let opened = try known(url)
        try FileManager.default.setAttributes([.modificationDate: Date().addingTimeInterval(3600)], ofItemAtPath: url.path)
        let result = try NoteWriter.write(try snapshot(of: url), to: url, known: opened, keepPrevious: nil)
        XCTAssertNil(result.conflictWith)
        XCTAssertEqual(result.saved.url, url)
        XCTAssertEqual(PDFRaw.annotations(inFile: url, pageIndex: 0).filter { $0.subtype == "Ink" }.count, 1)
        XCTAssertFalse(FileStore.exists(folder.appendingPathComponent("Note (iPad).pdf")))
    }

    @MainActor func testPreviousVersionOncePerSession() async throws {
        let folder = TestSupport.temporaryFolder()
        let url = note(in: folder)
        let original = try Data(contentsOf: url)
        let document = try await NoteDocument.open(url, libraryRoot: folder)
        let previous = SidecarLayout.previousVersion(forNote: url, libraryRoot: folder)
        let page = document.pageID(at: 0)
        document.setDrawing(PKDrawing(strokes: [TestSupport.stroke(wave(from: CGPoint(x: 60, y: 60), count: 10))]),
                            forPageID: page, undoable: true)
        await document.save()
        XCTAssertEqual(try Data(contentsOf: previous), original)
        var drawing = document.drawing(forPageID: page)
        drawing.strokes.append(TestSupport.stroke(wave(from: CGPoint(x: 60, y: 200), count: 10), date: baseDate + 9))
        document.setDrawing(drawing, forPageID: page, undoable: true)
        await document.save()
        XCTAssertEqual(try Data(contentsOf: previous), original, "previous.pdf is the file as it was before this session")
        XCTAssertEqual(PDFRaw.annotations(inFile: url, pageIndex: 0).filter { $0.subtype == "Ink" }.count, 2)
    }
}
