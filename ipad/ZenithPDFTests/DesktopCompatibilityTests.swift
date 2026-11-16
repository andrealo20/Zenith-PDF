import PDFKit
import PencilKit
import UIKit
import XCTest
@testable import ZenithPDF

/// desktop-ink.json, written by ipad/tools/make_fixtures.py: what the desktop reads from desktop-ink.pdf.
private struct DesktopFixture: Decodable {
    struct Ink: Decodable {
        let page: Int
        let id: String
        let paths: [[[Double]]]
        let widths: [[Double]]
        let color: [Double]
        let opacity: Double
        let highlighter: Bool
    }
    struct EditText: Decodable {
        let page: Int
        let contents: String
        let size: Double
    }
    let ink: [Ink]
    let annotations: Int
    let editText: EditText
}

final class DesktopCompatibilityTests: XCTestCase {
    private let baseDate = Date(timeIntervalSinceReferenceDate: 810_000_000)

    private func fixture() throws -> (pdf: URL, values: DesktopFixture) {
        guard let pdf = TestSupport.desktopFixture else { throw XCTSkip("desktop-ink.pdf was not generated (ci.sh fixtures)") }
        let json = TestSupport.fixture("desktop-ink.json")
        guard FileManager.default.fileExists(atPath: json.path) else { throw XCTSkip("desktop-ink.json was not generated") }
        return (pdf, try JSONDecoder().decode(DesktopFixture.self, from: Data(contentsOf: json)))
    }

    private func point(_ values: [Double]) -> CGPoint {
        CGPoint(x: values.first ?? 0, y: values.count > 1 ? values[1] : 0)
    }

    private func inks(_ url: URL, page: Int = 0) -> [RawAnnotation] {
        PDFRaw.annotations(inFile: url, pageIndex: page).filter { $0.subtype == "Ink" }
    }

    private func pngData() -> Data {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: CGSize(width: 60, height: 45), format: format).pngData { context in
            UIColor(red: 0.2, green: 0.5, blue: 0.8, alpha: 1).setFill()
            context.fill(CGRect(x: 0, y: 0, width: 60, height: 45))
        }
    }

    private func wave(from start: CGPoint, count: Int, height: CGFloat = 8) -> [CGPoint] {
        (0..<count).map { CGPoint(x: start.x + CGFloat($0) * 7, y: start.y + sin(CGFloat($0) / 3) * height) }
    }

    private func list(_ point: CGPoint) -> [Double] { [Double(point.x), Double(point.y)] }

    private func list(_ rect: CGRect) -> [Double] {
        [Double(rect.minX), Double(rect.minY), Double(rect.width), Double(rect.height)]
    }

    // MARK: The desktop fixture

    @MainActor func testDesktopFixtureReads() throws {
        let (url, values) = try fixture()
        let pdf = try XCTUnwrap(PDFDocument(url: url))
        let pages = NoteReader.extract(from: pdf)
        XCTAssertEqual(pages.count, pdf.pageCount)
        for (index, page) in pages.enumerated() {
            var expected: [(ink: DesktopFixture.Ink, points: [CGPoint], widths: [Double])] = []
            for ink in values.ink where ink.page == index {
                for (path, widths) in zip(ink.paths, ink.widths) {
                    expected.append((ink: ink, points: path.map { point($0) }, widths: widths))
                }
            }
            let strokes = page.drawing.strokes
            XCTAssertEqual(strokes.count, expected.count, "page \(index)")
            for (stroke, want) in zip(strokes, expected) {
                let label = "\(want.ink.id) on page \(index)"
                XCTAssertEqual(stroke.path.count, want.points.count * 3, label)
                for (i, control) in stroke.path.enumerated() where i / 3 < want.points.count {
                    let p = want.points[i / 3]
                    XCTAssertEqual(control.location.x, p.x, accuracy: 0.01, label)
                    XCTAssertEqual(control.location.y, p.y, accuracy: 0.01, label)
                    XCTAssertEqual(control.size.width, CGFloat(want.widths[i / 3]), accuracy: 0.01, label)
                }
                XCTAssertEqual(stroke.ink.inkType, want.ink.highlighter ? .marker : .pen, label)
                let style = StrokeCodec.style(of: stroke.ink)
                let color = RGB(r: want.ink.color[0], g: want.ink.color[1], b: want.ink.color[2])
                XCTAssertTrue(style.color.isClose(to: color, tolerance: 1.0 / 255 + 0.001), "\(label): \(style.color)")
                XCTAssertEqual(style.opacity, CGFloat(want.ink.opacity), accuracy: 0.01, label)
                XCTAssertEqual(stroke.path.creationDate, .distantPast, label)
            }
            XCTAssertEqual(pdf.page(at: index)?.annotations.filter { $0.type == "Ink" }.count, 0)
        }
    }

    @MainActor func testDesktopFixtureRoundTrip() async throws {
        let (source, values) = try fixture()
        let folder = TestSupport.temporaryFolder()
        let url = folder.appendingPathComponent("desktop-ink.pdf")
        try FileManager.default.copyItem(at: source, to: url)
        let document = try await NoteDocument.open(url, libraryRoot: folder)
        let page = try XCTUnwrap(document.pdf.page(at: values.editText.page))
        let box = try XCTUnwrap(page.annotations.first { TextBoxCodec.isTextBox($0) && $0.contents == values.editText.contents })
        var style = TextBoxCodec.style(of: box)
        XCTAssertTrue(style.bold)
        style.font = .times
        style.bold = false
        style.size = CGFloat(values.editText.size)
        TextBoxCodec.apply(text: values.editText.contents, style: style, to: box)
        document.markChanged()
        await document.save()
        XCTAssertNil(document.lastError)

        let edited = try XCTUnwrap(PDFRaw.annotations(inFile: url, pageIndex: values.editText.page)
            .first { $0.subtype == "FreeText" && $0.contents == values.editText.contents })
        XCTAssertEqual(edited.defaultStyle, TextBoxCodec.styleString(style))
        XCTAssertNil(edited.rectDifferences)
        for index in 0..<document.pdf.pageCount {
            let paths = values.ink.filter { $0.page == index }.reduce(0) { $0 + $1.paths.count }
            XCTAssertEqual(inks(url, page: index).count, paths, "one annotation per desktop path on page \(index)")
            let before = PDFRaw.annotations(inFile: source, pageIndex: index).map(\.subtype).filter { $0 != "Ink" }
            let after = PDFRaw.annotations(inFile: url, pageIndex: index).map(\.subtype).filter { $0 != "Ink" }
            XCTAssertEqual(before.sorted(), after.sorted(), "page \(index) kept its other annotations")
        }
        XCTAssertNotNil(PDFRaw.documentInfo(inFile: url)["ZenithNoteID"])
        if let export = TestSupport.exportFolder {
            try FileManager.default.createDirectory(at: export, withIntermediateDirectories: true)
            let target = export.appendingPathComponent("desktop-ink-ipad.pdf")
            try? FileManager.default.removeItem(at: target)
            try FileManager.default.copyItem(at: url, to: target)
        }
    }

    // MARK: Both ways with the desktop's own code (ci.sh desktop-check, desktop-roundtrip)

    @MainActor func testExportForDesktop() throws {
        guard let export = TestSupport.exportFolder else { throw XCTSkip("ZENITH_EXPORT_DIR is not set") }
        let pdf = TestSupport.pdf(pages: 1)
        let page = try XCTUnwrap(pdf.page(at: 0))
        let geometry = PageGeometry(page: page)
        // At least six strokes in a known order: desktop_edit.py edits them by position.
        let specs: [(PKInkingTool.InkType, UIColor, CGFloat, Int)] = [
            (.pen, .black, 2, 20), (.pen, UIColor(red: 0, green: 0.2, blue: 0.8, alpha: 1), 2.5, 20),
            (.pen, UIColor(red: 0.8, green: 0, blue: 0, alpha: 1), 1.8, 20), (.pen, UIColor(red: 0, green: 0.5, blue: 0, alpha: 1), 2, 40),
            (.pen, UIColor(white: 0.2, alpha: 1), 3, 20), (.marker, UIColor(red: 1, green: 0.85, blue: 0, alpha: 1), 12, 25),
            (.pencil, UIColor(red: 0.4, green: 0.2, blue: 0.6, alpha: 1), 1.5, 20),
        ]
        let strokes = specs.enumerated().map { item in
            TestSupport.stroke(wave(from: CGPoint(x: 60, y: 80 + CGFloat(item.offset) * 55), count: item.element.3),
                               width: item.element.2, ink: item.element.0, color: item.element.1, date: baseDate + Double(item.offset))
        }
        var textStyle = TextBoxStyle()
        textStyle.bold = true
        let text = "Typed on the iPad"
        let textBounds = TextBoxCodec.fittedBounds(text: text, style: textStyle, topLeft: CGPoint(x: 320, y: 300), width: 220)
        page.addAnnotation(TextBoxCodec.makeAnnotation(text: text, style: textStyle, bounds: textBounds))
        let pictureBounds = CGRect(x: 330, y: 120, width: 120, height: 90)
        page.addAnnotation(ImageAnnotation(bounds: pictureBounds, imageData: pngData()))
        let noteID = UUID().uuidString
        let drawing = PKDrawing(strokes: strokes)
        let data = try NoteWriter.render(NoteSnapshot(pdfData: try XCTUnwrap(pdf.dataRepresentation()), drawings: [drawing],
                                                      geometries: [geometry], pageCount: 1, noteID: noteID))
        try FileManager.default.createDirectory(at: export, withIntermediateDirectories: true)
        let url = export.appendingPathComponent("ipad-ink.pdf")
        try data.write(to: url)
        XCTAssertEqual(inks(url).count, strokes.count)

        let records = StrokeCodec.records(for: drawing, geometry: geometry)
        let strokeValues: [[String: Any]] = records.map { record in
            ["page": 0, "id": record.identifier,
             "paths": record.paths.map { $0.map { list(geometry.toDisplay($0)) } },
             "widths": record.widths.map { $0.map { Double($0) } },
             "color": [Double(record.color.r), Double(record.color.g), Double(record.color.b)],
             "opacity": Double(record.opacity), "highlighter": record.highlighter]
        }
        let textBoxes: [[String: Any]] = [["page": 0, "contents": text, "font": "Helvetica", "bold": true, "italic": false,
                                           "size": Double(textStyle.size), "rect": list(geometry.rectToDisplay(textBounds))]]
        let pictures: [[String: Any]] = [["page": 0, "rect": list(geometry.rectToDisplay(pictureBounds))]]
        let values: [String: Any] = ["format": "zenith-ipad-export-1", "noteID": noteID, "strokes": strokeValues,
                                     "textBoxes": textBoxes, "pictures": pictures]
        let json = try JSONSerialization.data(withJSONObject: values, options: [.prettyPrinted, .sortedKeys])
        try json.write(to: export.appendingPathComponent("ipad-ink.json"))
    }

    @MainActor func testReadsDesktopEdits() throws {
        guard let export = TestSupport.exportFolder else { throw XCTSkip("ZENITH_EXPORT_DIR is not set") }
        let original = export.appendingPathComponent("ipad-ink.pdf")
        let edited = export.appendingPathComponent("ipad-ink-desktop.pdf")
        let expectedURL = export.appendingPathComponent("ipad-ink-desktop.json")
        guard FileManager.default.fileExists(atPath: edited.path), FileManager.default.fileExists(atPath: original.path),
              FileManager.default.fileExists(atPath: expectedURL.path) else {
            throw XCTSkip("no desktop edit to read (ci.sh desktop-check writes it)")
        }
        let expected = try XCTUnwrap(try JSONSerialization.jsonObject(with: Data(contentsOf: expectedURL)) as? [String: Any])
        let entries = try XCTUnwrap(expected["strokes"] as? [[String: Any]])
        var stored: [String: [PKStroke]] = [:]
        for raw in inks(original) {
            guard let id = raw.identifier, let text = raw.pencilKit, let data = Data(base64Encoded: text) else { continue }
            stored[id] = try PKDrawing(data: data).strokes
        }
        var desktop: [String: RawAnnotation] = [:]
        for raw in inks(edited) { if let id = raw.identifier { desktop[id] = raw } }
        let pdf = try XCTUnwrap(PDFDocument(url: edited))
        let geometry = PageGeometry(page: try XCTUnwrap(pdf.page(at: 0)))
        var total = 0

        for entry in entries {
            let id = try XCTUnwrap(entry["id"] as? String)
            let expect = try XCTUnwrap(entry["expect"] as? String)
            let raw = try XCTUnwrap(desktop[id], "\(id) is missing from the desktop's file")
            let read = StrokeCodec.strokes(from: raw, geometry: geometry)
            total += read.count
            let label = "\(id) (\(expect))"
            if expect == "rebuilt" {
                let paths = try XCTUnwrap(entry["paths"] as? [[[Double]]])
                let widths = try XCTUnwrap(entry["widths"] as? [[Double]])
                XCTAssertEqual(read.count, paths.count, label)
                for (stroke, (points, ws)) in zip(read, zip(paths, widths)) {
                    XCTAssertEqual(stroke.path.creationDate, .distantPast, label)
                    XCTAssertEqual(stroke.path.count, points.count * 3, label)
                    for (i, control) in stroke.path.enumerated() where i / 3 < points.count {
                        XCTAssertEqual(control.location.x, CGFloat(points[i / 3][0]), accuracy: 0.02, label)
                        XCTAssertEqual(control.location.y, CGFloat(points[i / 3][1]), accuracy: 0.02, label)
                        XCTAssertEqual(control.size.width, CGFloat(ws[i / 3]), accuracy: 0.01, label)
                    }
                }
                continue
            }
            let before = try XCTUnwrap(stored[id], "\(id) has no stored strokes in the iPad's file")
            XCTAssertEqual(read.count, before.count, label)
            for (after, old) in zip(read, before) {
                XCTAssertEqual(after.path.creationDate.timeIntervalSinceReferenceDate,
                               old.path.creationDate.timeIntervalSinceReferenceDate, accuracy: 0.001, label)
                XCTAssertEqual(after.path.count, old.path.count, label)
                let a = after.path[0].location.applying(after.transform), b = old.path[0].location.applying(old.transform)
                switch expect {
                case "moved":
                    let offset = try XCTUnwrap(entry["offset"] as? [Double])
                    XCTAssertEqual(a.x - b.x, CGFloat(offset[0]), accuracy: 0.05, label)
                    XCTAssertEqual(a.y - b.y, CGFloat(offset[1]), accuracy: 0.05, label)
                    XCTAssertEqual(after.ink.inkType, old.ink.inkType, label)
                case "restyled":
                    let color = try XCTUnwrap(entry["color"] as? [Double])
                    let opacity = try XCTUnwrap(entry["opacity"] as? Double)
                    let scale = try XCTUnwrap(entry["widthScale"] as? Double)
                    let highlighter = try XCTUnwrap(entry["highlighter"] as? Bool)
                    let style = StrokeCodec.style(of: after.ink)
                    XCTAssertTrue(style.color.isClose(to: RGB(r: color[0], g: color[1], b: color[2]), tolerance: 0.01), label)
                    XCTAssertEqual(style.opacity, CGFloat(opacity), accuracy: 0.01, label)
                    XCTAssertEqual(style.highlighter, highlighter, label)
                    for (p, q) in zip(after.path, old.path) {
                        XCTAssertEqual(p.size.width, q.size.width * CGFloat(scale), accuracy: 0.02, label)
                    }
                    XCTAssertEqual(a.x, b.x, accuracy: 0.05, label)
                default:
                    XCTAssertEqual(after.transform, old.transform, label)
                    XCTAssertEqual(after.ink.inkType, old.ink.inkType, label)
                    XCTAssertTrue(RGB(after.ink.color).isClose(to: RGB(old.ink.color), tolerance: 0.005), label)
                }
            }
        }
        XCTAssertEqual(NoteReader.extract(from: pdf).first?.drawing.strokes.count, total)
        XCTAssertEqual(PDFRaw.documentInfo(inFile: edited)["ZenithNoteID"], expected["noteID"] as? String)
    }
}
