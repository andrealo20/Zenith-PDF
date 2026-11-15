import PDFKit
import UIKit
import XCTest
@testable import ZenithPDF

final class CoreTests: XCTestCase {
    private let a4Box = CGRect(x: 0, y: 0, width: 595, height: 842)

    private func assertClose(_ a: CGPoint, _ b: CGPoint, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(a.x, b.x, accuracy: 1e-9, file: file, line: line)
        XCTAssertEqual(a.y, b.y, accuracy: 1e-9, file: file, line: line)
    }

    // MARK: Geometry

    func testGeometryRoundTripsEveryRotation() {
        let p = CGPoint(x: 100, y: 200)
        // The table of section 3.4 on a 595 x 842 box.
        let expected: [Int: CGPoint] = [
            0: CGPoint(x: 100, y: 642),
            90: CGPoint(x: 200, y: 100),
            180: CGPoint(x: 495, y: 200),
            270: CGPoint(x: 642, y: 495),
        ]
        for rotation in [0, 90, 180, 270] {
            let geometry = PageGeometry(box: a4Box, rotation: rotation)
            let display = geometry.toDisplay(p)
            if let want = expected[rotation] { assertClose(display, want) }
            assertClose(geometry.toPDF(display), p)
            assertClose(geometry.toDisplay(geometry.toPDF(CGPoint(x: 12.5, y: 30.25))), CGPoint(x: 12.5, y: 30.25))
        }
        // An offset box round-trips too.
        let offset = PageGeometry(box: CGRect(x: 20, y: 40, width: 300, height: 500), rotation: 90)
        assertClose(offset.toPDF(offset.toDisplay(CGPoint(x: 77, y: 99))), CGPoint(x: 77, y: 99))
        // Rects come back as the same area.
        let rect = CGRect(x: 10, y: 20, width: 30, height: 40)
        for rotation in [0, 90, 180, 270] {
            let geometry = PageGeometry(box: a4Box, rotation: rotation)
            let back = geometry.rectToPDF(geometry.rectToDisplay(rect))
            XCTAssertEqual(back.minX, rect.minX, accuracy: 1e-9)
            XCTAssertEqual(back.minY, rect.minY, accuracy: 1e-9)
            XCTAssertEqual(back.width, rect.width, accuracy: 1e-9)
            XCTAssertEqual(back.height, rect.height, accuracy: 1e-9)
        }
        XCTAssertEqual(PageGeometry(box: a4Box, rotation: -90).rotation, 270)
        XCTAssertEqual(PageGeometry(box: a4Box, rotation: 450).rotation, 90)
    }

    func testGeometryCropBox() throws {
        let pdf = TestSupport.pdf(pages: 1, size: CGSize(width: 595, height: 842))
        let page = try XCTUnwrap(pdf.page(at: 0))
        page.setBounds(CGRect(x: 100, y: 50, width: 600, height: 900), for: .cropBox)
        let clipped = PageGeometry(page: page)
        XCTAssertEqual(clipped.box.minX, 100, accuracy: 0.001)
        XCTAssertEqual(clipped.box.minY, 50, accuracy: 0.001)
        XCTAssertEqual(clipped.box.maxX, 595, accuracy: 0.001)
        XCTAssertEqual(clipped.box.maxY, 842, accuracy: 0.001)

        // A crop box with nothing in common with the media box falls back to the media box.
        page.setBounds(CGRect(x: 700, y: 0, width: 100, height: 100), for: .cropBox)
        let fallback = PageGeometry(page: page)
        XCTAssertEqual(fallback.box.width, 595, accuracy: 0.001)
        XCTAssertEqual(fallback.box.height, 842, accuracy: 0.001)
    }

    func testDisplaySizeSwapsAtQuarterTurns() {
        XCTAssertEqual(PageGeometry(box: a4Box, rotation: 0).displaySize, CGSize(width: 595, height: 842))
        XCTAssertEqual(PageGeometry(box: a4Box, rotation: 90).displaySize, CGSize(width: 842, height: 595))
        XCTAssertEqual(PageGeometry(box: a4Box, rotation: 180).displaySize, CGSize(width: 595, height: 842))
        XCTAssertEqual(PageGeometry(box: a4Box, rotation: 270).displaySize, CGSize(width: 842, height: 595))
    }

    // MARK: Polylines

    func testSimplifyKeepsEndsAndCorners() {
        var corner: [CGPoint] = []
        for i in 0...10 { corner.append(CGPoint(x: CGFloat(i), y: 0)) }
        for i in 1...10 { corner.append(CGPoint(x: 10, y: CGFloat(i))) }
        XCTAssertEqual(PolylineMath.simplifyIndices(corner, tolerance: 0.15), [0, 10, 20])

        let straight = (0...20).map { CGPoint(x: CGFloat($0), y: CGFloat($0) * 0.5) }
        XCTAssertEqual(PolylineMath.simplifyIndices(straight, tolerance: 0.15), [0, 20])

        XCTAssertEqual(PolylineMath.simplifyIndices([CGPoint(x: 1, y: 1), CGPoint(x: 2, y: 2)], tolerance: 0.15), [0, 1])
        XCTAssertEqual(PolylineMath.simplifyIndices([], tolerance: 0.15), [])

        // A bump smaller than the tolerance goes, a bigger one stays.
        let small = [CGPoint(x: 0, y: 0), CGPoint(x: 5, y: 0.1), CGPoint(x: 10, y: 0)]
        XCTAssertEqual(PolylineMath.simplifyIndices(small, tolerance: 0.15), [0, 2])
        let big = [CGPoint(x: 0, y: 0), CGPoint(x: 5, y: 0.2), CGPoint(x: 10, y: 0)]
        XCTAssertEqual(PolylineMath.simplifyIndices(big, tolerance: 0.15), [0, 1, 2])
    }

    func testDistancesAndLength() {
        XCTAssertEqual(PolylineMath.distance(.zero, CGPoint(x: 3, y: 4)), 5, accuracy: 1e-9)
        XCTAssertEqual(PolylineMath.segmentDistance(CGPoint(x: 5, y: 2), .zero, CGPoint(x: 10, y: 0)), 2, accuracy: 1e-9)
        XCTAssertEqual(PolylineMath.segmentDistance(CGPoint(x: 13, y: 4), .zero, CGPoint(x: 10, y: 0)), 5, accuracy: 1e-9)
        XCTAssertEqual(PolylineMath.segmentDistance(CGPoint(x: 3, y: 4), .zero, .zero), 5, accuracy: 1e-9)
        XCTAssertEqual(PolylineMath.pathLength([.zero, CGPoint(x: 3, y: 4), CGPoint(x: 3, y: 10)]), 11, accuracy: 1e-9)
        XCTAssertEqual(PolylineMath.pathLength([.zero]), 0)
    }

    // MARK: Sidecars

    func testSidecarFolderNextToNote() {
        let root = TestSupport.temporaryFolder()
        let note = root.appendingPathComponent("Physics", isDirectory: true).appendingPathComponent("Lecture 1.pdf")
        let folder = SidecarLayout.folder(forNote: note, libraryRoot: root)
        let expected = root.appendingPathComponent("Physics/.zenith/Lecture 1.pdf", isDirectory: true)
        XCTAssertEqual(folder.standardizedFileURL.path, expected.standardizedFileURL.path)
        XCTAssertEqual(SidecarLayout.metadata(forNote: note, libraryRoot: root).path, folder.appendingPathComponent("meta.json").path)
        XCTAssertEqual(SidecarLayout.searchIndex(forNote: note, libraryRoot: root).path, folder.appendingPathComponent("search.json").path)
        XCTAssertEqual(SidecarLayout.recordings(forNote: note, libraryRoot: root).path, folder.appendingPathComponent("recordings.json").path)
        XCTAssertEqual(SidecarLayout.audioFolder(forNote: note, libraryRoot: root).path, folder.appendingPathComponent("audio").path)
        XCTAssertEqual(SidecarLayout.previousVersion(forNote: note, libraryRoot: root).path, folder.appendingPathComponent("previous.pdf").path)

        let atRoot = root.appendingPathComponent("Loose.pdf")
        XCTAssertEqual(SidecarLayout.folder(forNote: atRoot, libraryRoot: root).standardizedFileURL.path,
                       root.appendingPathComponent(".zenith/Loose.pdf").standardizedFileURL.path)
    }

    func testSidecarFolderForExternalFile() throws {
        let root = TestSupport.temporaryFolder()
        let outside = TestSupport.temporaryFolder().appendingPathComponent("Shared.pdf")
        let folder = SidecarLayout.folder(forNote: outside, libraryRoot: root)
        XCTAssertEqual(folder.deletingLastPathComponent().lastPathComponent, "External")
        let support = try XCTUnwrap(FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first)
        XCTAssertTrue(SidecarLayout.isInside(folder, root: support))
        let name = folder.lastPathComponent
        XCTAssertEqual(name.count, 16)
        XCTAssertTrue(name.allSatisfy { "0123456789abcdef".contains($0) })
        XCTAssertEqual(SidecarLayout.folder(forNote: outside, libraryRoot: nil), folder, "no library behaves as outside it")
        XCTAssertEqual(SidecarLayout.folder(forNote: outside, libraryRoot: root), folder, "stable")
        let other = outside.deletingLastPathComponent().appendingPathComponent("Other.pdf")
        XCTAssertNotEqual(SidecarLayout.folder(forNote: other, libraryRoot: root), folder)
    }

    func testHiddenNames() {
        XCTAssertTrue(SidecarLayout.isHidden(URL(fileURLWithPath: "/tmp/.zenith", isDirectory: true)))
        XCTAssertTrue(SidecarLayout.isHidden(URL(fileURLWithPath: "/tmp/.zenith-trash", isDirectory: true)))
        XCTAssertTrue(SidecarLayout.isHidden(URL(fileURLWithPath: "/tmp/.DS_Store")))
        XCTAssertFalse(SidecarLayout.isHidden(URL(fileURLWithPath: "/tmp/Notes.pdf")))
        XCTAssertFalse(SidecarLayout.isHidden(URL(fileURLWithPath: "/tmp/.hidden/Visible.pdf")))
        XCTAssertEqual(SidecarLayout.folderName, ".zenith")
        XCTAssertEqual(SidecarLayout.trashName, ".zenith-trash")
    }

    func testIsInsideResolvesPrivateVar() {
        let root = URL(fileURLWithPath: "/private/var/mobile/Library", isDirectory: true)
        let note = URL(fileURLWithPath: "/var/mobile/Library/Physics/Lecture.pdf")
        XCTAssertTrue(SidecarLayout.isInside(note, root: root))
        XCTAssertTrue(SidecarLayout.isInside(URL(fileURLWithPath: "/private/var/mobile/Library/a.pdf"),
                                             root: URL(fileURLWithPath: "/var/mobile/Library", isDirectory: true)))
        XCTAssertTrue(SidecarLayout.isInside(URL(fileURLWithPath: "/var/mobile/Library/x/../a.pdf"), root: root))
        XCTAssertFalse(SidecarLayout.isInside(URL(fileURLWithPath: "/var/mobile/LibraryOther/a.pdf"), root: root))
        XCTAssertFalse(SidecarLayout.isInside(URL(fileURLWithPath: "/var/mobile/a.pdf"), root: root))

        let temp = TestSupport.temporaryFolder()
        XCTAssertTrue(SidecarLayout.isInside(temp.appendingPathComponent("n.pdf"), root: temp))
        XCTAssertTrue(SidecarLayout.isInside(temp.resolvingSymlinksInPath().appendingPathComponent("n.pdf"), root: temp))
    }

    // MARK: Model

    func testRGBFromDynamicColorUsesLightAppearance() {
        let color = Folio.dynamic(light: 0xFF0000, dark: 0x00FF00)
        let rgb = RGB(color)
        XCTAssertTrue(rgb.isClose(to: RGB(r: 1, g: 0, b: 0)), "\(rgb)")

        let label = RGB(UIColor.label)
        XCTAssertTrue(label.isClose(to: .black), "label resolves to black in the light appearance, got \(label)")

        let extended = RGB(UIColor(red: 1.4, green: -0.2, blue: 0.5, alpha: 1))
        XCTAssertEqual(extended.r, 1)
        XCTAssertEqual(extended.g, 0)
        XCTAssertEqual(extended.b, 0.5, accuracy: 0.001)

        let grey = RGB(UIColor(white: 0.5, alpha: 1))
        XCTAssertTrue(grey.isClose(to: RGB(r: 0.5, g: 0.5, b: 0.5)), "\(grey)")

        XCTAssertTrue(RGB(RGB(r: 0.2, g: 0.4, b: 0.6).uiColor(alpha: 0.3)).isClose(to: RGB(r: 0.2, g: 0.4, b: 0.6)))
        XCTAssertFalse(RGB(r: 0.2, g: 0.4, b: 0.6).isClose(to: RGB(r: 0.21, g: 0.4, b: 0.6)))
    }

    func testTextFontNames() {
        XCTAssertEqual(TextFont.helvetica.postScriptName(bold: false, italic: false), "Helvetica")
        XCTAssertEqual(TextFont.helvetica.postScriptName(bold: true, italic: true), "Helvetica-BoldOblique")
        XCTAssertEqual(TextFont.times.postScriptName(bold: false, italic: false), "Times-Roman")
        XCTAssertEqual(TextFont.times.postScriptName(bold: false, italic: true), "Times-Italic")
        XCTAssertEqual(TextFont.times.postScriptName(bold: true, italic: true), "Times-BoldItalic")
        XCTAssertEqual(TextFont.courier.postScriptName(bold: true, italic: false), "Courier-Bold")
        XCTAssertEqual(TextFont.courier.postScriptName(bold: false, italic: true), "Courier-Oblique")
        var style = TextBoxStyle()
        style.size = 20
        XCTAssertEqual(style.uiFont.pointSize, 20, accuracy: 0.001)
    }

    func testPaperSizes() throws {
        XCTAssertEqual(PaperSize.a4.points.width, 595.28, accuracy: 0.001)
        XCTAssertEqual(PaperSize.a4.points.height, 841.89, accuracy: 0.001)
        XCTAssertEqual(PaperSize.letter.points, CGSize(width: 612, height: 792))
        XCTAssertEqual(PaperTemplate.standard, PaperTemplate(pattern: .lined, color: .white, size: .a4))
        XCTAssertTrue(PaperColor.dark.isDark)
        XCTAssertFalse(PaperColor.cream.isDark)
        XCTAssertTrue(PaperColor.cream.fill.isClose(to: RGB(r: 252.0 / 255.0, g: 250.0 / 255.0, b: 244.0 / 255.0)))

        for pattern in PaperPattern.allCases {
            for color in PaperColor.allCases {
                for size in PaperSize.allCases {
                    let template = PaperTemplate(pattern: pattern, color: color, size: size)
                    let data = try JSONEncoder().encode(template)
                    XCTAssertEqual(try JSONDecoder().decode(PaperTemplate.self, from: data), template)
                }
            }
        }
    }

    func testMetadataDecodesWithoutNoteID() throws {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        let older = """
        {"created":"2026-01-02T03:04:05Z","isFavourite":true,"lastPage":3,
         "template":{"color":"cream","pattern":"grid","size":"letter"}}
        """
        let metadata = try decoder.decode(NoteMetadata.self, from: Data(older.utf8))
        XCTAssertTrue(metadata.isFavourite)
        XCTAssertEqual(metadata.lastPage, 3)
        XCTAssertEqual(metadata.template, PaperTemplate(pattern: .grid, color: .cream, size: .letter))
        XCTAssertEqual(metadata.created, Date(timeIntervalSince1970: 1_767_323_045))
        XCTAssertNil(metadata.noteID)

        XCTAssertEqual(try decoder.decode(NoteMetadata.self, from: Data("{}".utf8)), NoteMetadata())

        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        var full = metadata
        full.noteID = "ABC"
        XCTAssertEqual(try decoder.decode(NoteMetadata.self, from: try encoder.encode(full)), full)

        var style = TextBoxStyle()
        style.font = .times
        style.bold = true
        XCTAssertEqual(try JSONDecoder().decode(TextBoxStyle.self, from: try JSONEncoder().encode(style)), style)
        XCTAssertEqual(try JSONDecoder().decode(TextBoxStyle.self, from: Data("{\"size\":18}".utf8)).size, 18)
    }

    // MARK: Localization, errors

    func testTrFormatsAndFallsBackToKey() {
        XCTAssertEqual(tr("Page %lld of %lld", Int64(2), Int64(5)), "Page 2 of 5")
        XCTAssertEqual(tr("zenith.test.missing-key"), "zenith.test.missing-key")
        XCTAssertEqual(tr("Version %@", "1.2" as NSString), "Version 1.2")
        XCTAssertEqual(tr("100%"), "100%", "no arguments: the key is not used as a format")
        XCTAssertEqual(ZenithError.nameTaken("A").errorDescription, "A note or folder with this name already exists.")
        let copy = URL(fileURLWithPath: "/tmp/Lecture (iPad).pdf")
        XCTAssertEqual(ZenithError.conflict(original: URL(fileURLWithPath: "/tmp/Lecture.pdf"), copy: copy).errorDescription,
                       "The file changed on another device. Your changes were saved as \"Lecture (iPad)\".")
    }

    // MARK: Debouncer

    @MainActor func testDebouncerRunsLastActionOnce() async throws {
        let debouncer = Debouncer(seconds: 0.1)
        let runs = RunRecord()
        debouncer.schedule { runs.values.append(1) }
        debouncer.schedule { runs.values.append(2) }
        debouncer.schedule { runs.values.append(3) }
        XCTAssertEqual(runs.values, [])
        try await Task.sleep(nanoseconds: 400_000_000)
        XCTAssertEqual(runs.values, [3])

        debouncer.schedule { runs.values.append(4) }
        debouncer.flush()
        XCTAssertEqual(runs.values, [3, 4])
        debouncer.flush()
        XCTAssertEqual(runs.values, [3, 4], "flush with nothing pending does nothing")

        debouncer.schedule { runs.values.append(5) }
        debouncer.cancel()
        try await Task.sleep(nanoseconds: 300_000_000)
        XCTAssertEqual(runs.values, [3, 4])
    }
}

/// What the debouncer ran, shared with its actions.
@MainActor private final class RunRecord {
    var values: [Int] = []
}
