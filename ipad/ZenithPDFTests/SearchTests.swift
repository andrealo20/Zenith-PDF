import CryptoKit
import PDFKit
import PencilKit
import UIKit
import XCTest
@testable import ZenithPDF

final class SearchTests: XCTestCase {
    /// nil until probed; false when Vision cannot read text drawn with a system font on this simulator.
    nonisolated(unsafe) private static var visionWorks: Bool?

    private func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private func item(_ url: URL) -> NoteItem {
        NoteItem(url: url, title: url.deletingPathExtension().lastPathComponent, modified: Date(), created: Date(),
                 isFavourite: false, folderPath: "")
    }

    /// "Zenith notes" in a 72 pt system font, rendered at scale 2 on an 800 x 240 page.
    @MainActor private func recogniseRenderedText() async throws -> (words: [InkWord], drawn: CGRect) {
        let size = CGSize(width: 800, height: 240)
        let text = "Zenith notes" as NSString
        let attributes: [NSAttributedString.Key: Any] = [.font: UIFont.systemFont(ofSize: 72), .foregroundColor: UIColor.black]
        let origin = CGPoint(x: 40, y: 60)
        let drawn = CGRect(origin: origin, size: text.size(withAttributes: attributes))
        let format = UIGraphicsImageRendererFormat()
        format.scale = 2
        format.opaque = true
        let image = UIGraphicsImageRenderer(size: size, format: format).image { context in
            UIColor.white.setFill()
            context.fill(CGRect(origin: .zero, size: size))
            text.draw(at: origin, withAttributes: attributes)
        }
        let cgImage = try XCTUnwrap(image.cgImage)
        return (try await HandwritingRecognizer.words(in: cgImage, pageSize: size), drawn)
    }

    @MainActor private func requireVision() async throws {
        if Self.visionWorks == nil {
            let found = ((try? await recogniseRenderedText())?.words ?? []).map { $0.text.searchFolded }
            Self.visionWorks = found.contains { $0.hasPrefix("zenith") } && found.contains { $0.hasPrefix("notes") }
        }
        if Self.visionWorks != true { throw XCTSkip("Vision unavailable on this simulator") }
    }

    // MARK: Index and matching

    func testFolding() {
        XCTAssertEqual("ÉCOLE Ünïcode".searchFolded, "ecole unicode".searchFolded)
        XCTAssertEqual("Ça".searchFolded, "CA".searchFolded)
        let index = SearchIndex(pages: [PageText(typed: "L'école de Zürich", words: [], inkHash: "")])
        XCTAssertEqual(index.hits(for: "ECOLE").count, 1)
        XCTAssertEqual(index.hits(for: "zurich").count, 1)
        XCTAssertTrue(index.hits(for: "eclair").isEmpty)
    }

    func testPrefixMatchingEveryWord() {
        let gravity = CGRect(x: 10, y: 20, width: 80, height: 30)
        let index = SearchIndex(pages: [
            PageText(typed: "Second law of motion", words: [], inkHash: ""),
            PageText(typed: "Momentum", words: [InkWord(text: "gravity", rect: gravity)], inkHash: ""),
        ])
        XCTAssertEqual(index.hits(for: "sec mot").map(\.pageIndex), [0])
        XCTAssertEqual(index.hits(for: "law,").map(\.pageIndex), [0])
        // Words may come from typed text and handwriting of the same page.
        XCTAssertEqual(index.hits(for: "mom grav").map(\.pageIndex), [1, 1])
        // Every word must be on one page, and each must start a word.
        XCTAssertTrue(index.hits(for: "sec grav").isEmpty)
        XCTAssertTrue(index.hits(for: "econd").isEmpty)
        XCTAssertTrue(index.hits(for: "").isEmpty)
        XCTAssertTrue(index.hits(for: " , ").isEmpty)
        let typed = index.hits(for: "motion")
        XCTAssertEqual(typed.count, 1)
        XCTAssertEqual(typed.first?.inInk, false)
        XCTAssertEqual(typed.first?.snippet, "Second law of motion")
    }

    func testHitsCarryRects() {
        let newton = CGRect(x: 40, y: 100, width: 90, height: 28)
        let laws = CGRect(x: 140, y: 100, width: 50, height: 28)
        let index = SearchIndex(pages: [
            PageText(typed: "", words: [], inkHash: ""),
            PageText(typed: "", words: [], inkHash: ""),
            PageText(typed: "Newtonian mechanics",
                     words: [InkWord(text: "Newton", rect: newton), InkWord(text: "laws", rect: laws)], inkHash: "a"),
        ])
        let hits = index.hits(for: "newt")
        XCTAssertEqual(hits.map(\.id), ["2-0", "2-1"])
        XCTAssertEqual(hits.first?.pageIndex, 2)
        XCTAssertEqual(hits.first?.inInk, true)
        XCTAssertEqual(hits.first?.rects, [newton])
        XCTAssertEqual(hits.first?.snippet, "Newton laws")
        XCTAssertEqual(hits.last?.inInk, false)
        XCTAssertEqual(hits.last?.rects, [])
        XCTAssertEqual(index.hits(for: "laws newton").first?.rects, [newton, laws])
    }

    func testIndexRoundTrip() throws {
        let root = TestSupport.temporaryFolder()
        let note = TestSupport.writeNote(TestSupport.pdf(pages: 2), named: "Round trip", in: root)
        XCTAssertNil(SearchIndex.load(note: note, libraryRoot: root))
        let index = SearchIndex(pages: [
            PageText(typed: "Typed text", words: [], inkHash: "0f"),
            PageText(typed: "", words: [InkWord(text: "Zenith", rect: CGRect(x: 1.5, y: 2.25, width: 30, height: 12))],
                     inkHash: "ab"),
        ])
        try index.save(note: note, libraryRoot: root)
        let file = SidecarLayout.searchIndex(forNote: note, libraryRoot: root)
        XCTAssertTrue(FileManager.default.fileExists(atPath: file.path))
        let loaded = try XCTUnwrap(SearchIndex.load(note: note, libraryRoot: root))
        XCTAssertEqual(loaded.version, 1)
        XCTAssertEqual(loaded.pages, index.pages)
        try Data("not json".utf8).write(to: file)
        XCTAssertNil(SearchIndex.load(note: note, libraryRoot: root))
    }

    // MARK: Vision

    @MainActor func testRecognisesRenderedText() async throws {
        let result: (words: [InkWord], drawn: CGRect)
        do {
            result = try await recogniseRenderedText()
        } catch {
            Self.visionWorks = false
            throw XCTSkip("Vision unavailable on this simulator: \(error)")
        }
        if result.words.isEmpty {
            Self.visionWorks = false
            throw XCTSkip("Vision unavailable on this simulator")
        }
        let zenith = result.words.first { $0.text.searchFolded.hasPrefix("zenith") }
        let notes = result.words.first { $0.text.searchFolded.hasPrefix("notes") }
        Self.visionWorks = zenith != nil && notes != nil
        XCTAssertNotNil(zenith, "recognised \(result.words.map(\.text))")
        XCTAssertNotNil(notes, "recognised \(result.words.map(\.text))")
        // Rects are in page space (the image was twice as large) and in reading order.
        let area = result.drawn.insetBy(dx: -24, dy: -24)
        for word in result.words {
            XCTAssertTrue(area.contains(word.rect), "\(word.rect) outside \(area)")
        }
        if let zenith, let notes {
            XCTAssertLessThan(zenith.rect.minX, notes.rect.minX)
        }
    }

    @MainActor func testRecognisesDemoInk() async throws {
        let origin = CGPoint(x: 60, y: 160)
        let strokes = DemoInk.strokes("Zenith notes", at: origin, size: 56, ink: PKInk(.pen, color: .black),
                                      startingAt: Date(), secondsPerStroke: 0.2)
        try XCTSkipIf(strokes.isEmpty, "DemoInk draws nothing yet")
        try await requireVision()
        let pageSize = PaperSize.a4.points
        let image = try XCTUnwrap(HandwritingRecognizer.image(of: PKDrawing(strokes: strokes), pageSize: pageSize))
        XCTAssertGreaterThanOrEqual(image.width, Int(pageSize.width * 2))
        let words = try await HandwritingRecognizer.words(in: image, pageSize: pageSize)
        let index = SearchIndex(pages: [PageText(typed: "", words: words, inkHash: "")])
        XCTAssertFalse(index.hits(for: "zenith notes").isEmpty, "recognised \(words.map(\.text))")
        if let expected = DemoInk.words("Zenith notes", at: origin, size: 56).first,
           let hit = index.hits(for: "zenith").first {
            XCTAssertTrue(hit.rects.contains { $0.intersects(expected.rect) }, "\(hit.rects) misses \(expected.rect)")
        }
    }

    @MainActor func testUnchangedPagesSkipped() async throws {
        let folder = TestSupport.temporaryFolder()
        let url = TestSupport.writeNote(TestSupport.pdf(pages: 2), named: "Skip", in: folder)
        let document = try await NoteDocument.open(url, libraryRoot: folder)
        let line = TestSupport.stroke([CGPoint(x: 60, y: 100), CGPoint(x: 200, y: 112), CGPoint(x: 340, y: 100)], width: 3)
        document.setDrawing(PKDrawing(strokes: [line]), forPageID: document.pageID(at: 0), undoable: false)
        document.setDrawing(PKDrawing(strokes: [line]), forPageID: document.pageID(at: 1), undoable: false)
        await SearchIndexer.shared.indexNow(document)

        var index = try XCTUnwrap(SearchIndex.load(note: document.url, libraryRoot: folder))
        XCTAssertEqual(index.pages.count, 2)
        XCTAssertEqual(index.pages.first?.inkHash, SearchIndex.inkHash(of: document.drawing(forPageID: document.pageID(at: 0))))
        // Words no recogniser would produce: they survive only if the page is skipped.
        let marker = [InkWord(text: "untouched", rect: CGRect(x: 1, y: 2, width: 3, height: 4))]
        index.pages[0].words = marker
        index.pages[1].words = marker
        try index.save(note: document.url, libraryRoot: folder)

        let more = TestSupport.stroke([CGPoint(x: 60, y: 300), CGPoint(x: 340, y: 320)], width: 3)
        document.setDrawing(PKDrawing(strokes: [line, more]), forPageID: document.pageID(at: 1), undoable: false)
        await SearchIndexer.shared.indexNow(document)

        let after = try XCTUnwrap(SearchIndex.load(note: document.url, libraryRoot: folder))
        XCTAssertEqual(after.pages[0].words, marker)
        XCTAssertEqual(after.pages[0].inkHash, index.pages[0].inkHash)
        XCTAssertNotEqual(after.pages[1].words, marker)
        XCTAssertNotEqual(after.pages[1].inkHash, index.pages[1].inkHash)
    }

    // MARK: Library and note search

    @MainActor func testLibrarySearchTitleAndInk() async throws {
        let root = TestSupport.temporaryFolder()
        let physics = TestSupport.writeNote(TestSupport.pdf(pages: 1), named: "Physics lecture", in: root)
        let recipes = TestSupport.writeNote(TestSupport.pdf(pages: 2), named: "Recipes", in: root)
        let renderer = UIGraphicsPDFRenderer(bounds: CGRect(origin: .zero, size: PaperSize.a4.points))
        let data = renderer.pdfData { context in
            context.beginPage()
            ("Gravity waves" as NSString).draw(at: CGPoint(x: 72, y: 72), withAttributes: [.font: UIFont.systemFont(ofSize: 18)])
        }
        let lab = TestSupport.writeNote(try XCTUnwrap(PDFDocument(data: data)), named: "Lab", in: root)
        let newton = CGRect(x: 30, y: 40, width: 80, height: 24)
        try SearchIndex(pages: [
            PageText(typed: "", words: [], inkHash: ""),
            PageText(typed: "", words: [InkWord(text: "Newton", rect: newton)], inkHash: "x"),
        ]).save(note: recipes, libraryRoot: root)
        let notes = [physics, recipes, lab].map { item($0) }

        let byTitle = await LibrarySearch.search("phys", in: notes, libraryRoot: root)
        XCTAssertEqual(byTitle.map(\.id), [physics])
        XCTAssertEqual(byTitle.first?.titleMatch, true)
        XCTAssertEqual(byTitle.first?.hits.isEmpty, true)

        let byInk = await LibrarySearch.search("NEWT", in: notes, libraryRoot: root)
        XCTAssertEqual(byInk.map(\.id), [recipes])
        XCTAssertEqual(byInk.first?.titleMatch, false)
        XCTAssertEqual(byInk.first?.hits.first?.pageIndex, 1)
        XCTAssertEqual(byInk.first?.hits.first?.inInk, true)
        XCTAssertEqual(byInk.first?.hits.first?.rects, [newton])

        // A note without an index is searched by its typed text.
        let byTyped = await LibrarySearch.search("grav", in: notes, libraryRoot: root)
        XCTAssertEqual(byTyped.map(\.id), [lab])
        XCTAssertEqual(byTyped.first?.hits.first?.inInk, false)

        let empty = await LibrarySearch.search("  ", in: notes, libraryRoot: root)
        XCTAssertTrue(empty.isEmpty)
    }

    @MainActor func testNoteSearchHighlights() async throws {
        let folder = TestSupport.temporaryFolder()
        let url = TestSupport.writeNote(TestSupport.pdf(pages: 2), named: "Highlights", in: folder)
        let document = try await NoteDocument.open(url, libraryRoot: folder)
        let editor = EditorViewController(document: document)
        let zenith = CGRect(x: 50, y: 80, width: 120, height: 40)
        let notes = CGRect(x: 180, y: 80, width: 100, height: 40)
        // The ink hashes match the note's drawings, so the indexer keeps these words.
        let pages = (0..<2).map { index in
            PageText(typed: "", words: index == 1 ? [InkWord(text: "Zenith", rect: zenith), InkWord(text: "notes", rect: notes)] : [],
                     inkHash: SearchIndex.inkHash(of: document.drawing(forPageID: document.pageID(at: index))))
        }
        try SearchIndex(pages: pages).save(note: document.url, libraryRoot: folder)
        let model = NoteSearchModel(document: document, editor: editor)

        model.query = "zen"
        await model.run()
        XCTAssertFalse(model.isIndexing)
        XCTAssertEqual(model.hits.map(\.pageIndex), [1])
        XCTAssertEqual(model.hits.first?.rects, [zenith])
        XCTAssertEqual(model.highlights, [1: [zenith]])

        model.query = "notes zen"
        await model.run()
        XCTAssertEqual(model.highlights, [1: [zenith, notes]])

        let hit = try XCTUnwrap(model.hits.first)
        model.select(hit)
        XCTAssertEqual(model.selectedHitID, hit.id)
        XCTAssertEqual(model.highlights, [1: hit.rects])

        model.query = "absent"
        await model.run()
        XCTAssertTrue(model.hits.isEmpty)
        XCTAssertEqual(model.highlights, [:])

        model.query = "zen"
        await model.run()
        model.clear()
        XCTAssertEqual(model.query, "")
        XCTAssertTrue(model.hits.isEmpty)
        XCTAssertEqual(model.highlights, [:])
        XCTAssertNil(model.selectedHitID)
    }
}
