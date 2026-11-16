import PDFKit
import PencilKit
import UIKit
import XCTest
@testable import ZenithPDF

final class PageOperationTests: XCTestCase {
    // MARK: - Helpers

    @MainActor private func openNote(pages: Int, size: CGSize = PaperSize.a4.points) async throws -> NoteDocument {
        let folder = TestSupport.temporaryFolder()
        let url = TestSupport.writeNote(TestSupport.pdf(pages: pages, size: size), named: "Note", in: folder)
        return try await NoteDocument.open(url, libraryRoot: folder)
    }

    @MainActor private func drawing(strokes count: Int, y: CGFloat = 100) -> PKDrawing {
        PKDrawing(strokes: (0..<count).map { n in
            TestSupport.stroke([CGPoint(x: 50, y: y + CGFloat(n) * 20), CGPoint(x: 300, y: y + CGFloat(n) * 20)],
                               width: 3, date: Date(timeIntervalSince1970: 1_000_000 + Double(n)))
        })
    }

    @MainActor private func assertAligned(_ document: NoteDocument, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(document.pages.count, document.pdf.pageCount, "pages and PDF pages out of step", file: file, line: line)
    }

    @MainActor private func pictureData() -> Data {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let image = UIGraphicsImageRenderer(size: CGSize(width: 40, height: 30), format: format).image { context in
            UIColor.systemRed.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 40, height: 30))
        }
        return image.pngData() ?? Data()
    }

    @MainActor private func photo(width: CGFloat, height: CGFloat) -> UIImage {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: CGSize(width: width, height: height), format: format).image { context in
            UIColor.systemBlue.setFill()
            context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        }
    }

    // MARK: - Tests

    @MainActor func testAddPageUsesNoteTemplate() async throws {
        let document = try await openNote(pages: 2)
        document.template = PaperTemplate(pattern: .lined, color: .dark, size: .letter)
        let before = document.pages.map(\.id)
        document.addPage(at: 1)
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 3)
        XCTAssertEqual(document.pages[0].id, before[0])
        XCTAssertEqual(document.pages[2].id, before[1])
        XCTAssertFalse(document.autoAddedPageIDs.contains(document.pages[1].id))
        guard let added = document.pdf.page(at: 1) else { return XCTFail("no page") }
        XCTAssertEqual(added.bounds(for: .mediaBox).width, PaperSize.letter.points.width, accuracy: 0.01)
        XCTAssertEqual(added.bounds(for: .mediaBox).height, PaperSize.letter.points.height, accuracy: 0.01)
        let image = added.thumbnail(of: CGSize(width: 200, height: 260), for: .mediaBox)
        // Right of where the rules end, so the pixel is paper.
        let paper = TemplateTests.pixel(of: image, at: CGPoint(x: 0.98, y: 0.5))
        XCTAssertEqual(paper?.r ?? 1, PaperColor.dark.fill.r, accuracy: 0.05, "the note's dark paper")

        document.addPage(at: 99, template: PaperTemplate(pattern: .blank, color: .white, size: .a4))
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 4)
        XCTAssertEqual(document.pdf.page(at: 3)?.bounds(for: .mediaBox).height ?? 0, PaperSize.a4.points.height, accuracy: 0.01)
    }

    @MainActor func testDuplicateCopiesInk() async throws {
        let document = try await openNote(pages: 2)
        let source = document.pageID(at: 0)
        document.setDrawing(drawing(strokes: 3), forPageID: source, undoable: false)
        document.duplicatePage(at: 0)
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 3)
        XCTAssertEqual(document.pages[0].id, source)
        let copy = document.pages[1]
        XCTAssertNotEqual(copy.id, source)
        XCTAssertEqual(copy.drawing.strokes.count, 3)
        XCTAssertEqual(copy.drawing.dataRepresentation(), document.pages[0].drawing.dataRepresentation())
        XCTAssertFalse(document.pdf.page(at: 1) === document.pdf.page(at: 0), "a fresh page object")

        document.undoManager.undo()
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 2)
        XCTAssertEqual(document.pages[0].drawing.strokes.count, 3, "the original keeps its ink")
    }

    @MainActor func testDuplicatePageWithPicture() async throws {
        let document = try await openNote(pages: 1)
        let bytes = pictureData()
        XCTAssertFalse(bytes.isEmpty)
        guard let page = document.pdf.page(at: 0) else { return XCTFail("no page") }
        let picture = ImageAnnotation(bounds: CGRect(x: 100, y: 500, width: 160, height: 120), imageData: bytes)
        page.addAnnotation(picture)
        document.markChanged()

        document.duplicatePage(at: 0)
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 2)
        guard let duplicate = document.pdf.page(at: 1) else { return XCTFail("no duplicate") }
        let pictures = duplicate.annotations.compactMap { $0 as? ImageAnnotation }
        XCTAssertEqual(pictures.count, 1)
        XCTAssertFalse(pictures.first === picture, "a new object, never a copy of the original")
        XCTAssertEqual(pictures.first?.imageData, bytes)
        XCTAssertEqual(page.annotations.compactMap { $0 as? ImageAnnotation }.count, 1, "the original page is untouched")
    }

    @MainActor func testDeleteAndUndo() async throws {
        let document = try await openNote(pages: 3)
        let ids = document.pages.map(\.id)
        document.setDrawing(drawing(strokes: 2), forPageID: ids[1], undoable: false)
        let deletedPage = document.pdf.page(at: 1)
        var pageChanges = 0
        document.onPagesChanged = { pageChanges += 1 }
        let revision = document.revision

        document.deletePage(at: 1)
        assertAligned(document)
        XCTAssertEqual(document.pages.map(\.id), [ids[0], ids[2]])
        XCTAssertEqual(pageChanges, 1)
        XCTAssertGreaterThan(document.revision, revision, "markChanged ran")
        XCTAssertTrue(document.undoManager.canUndo)

        document.undoManager.undo()
        assertAligned(document)
        XCTAssertEqual(document.pages.map(\.id), ids)
        XCTAssertEqual(document.pages[1].drawing.strokes.count, 2)
        XCTAssertTrue(document.pdf.page(at: 1) === deletedPage)
        XCTAssertEqual(pageChanges, 2)

        document.undoManager.redo()
        assertAligned(document)
        XCTAssertEqual(document.pages.map(\.id), [ids[0], ids[2]])
    }

    @MainActor func testReorderKeepsInkWithPage() async throws {
        let document = try await openNote(pages: 3)
        let ids = document.pages.map(\.id)
        let pdfPages = (0..<3).compactMap { document.pdf.page(at: $0) }
        for (n, id) in ids.enumerated() {
            document.setDrawing(drawing(strokes: n + 1), forPageID: id, undoable: false)
        }

        document.reorderPage(from: 0, to: 2)
        assertAligned(document)
        XCTAssertEqual(document.pages.map(\.id), [ids[1], ids[2], ids[0]])
        XCTAssertEqual(document.pages.map { $0.drawing.strokes.count }, [2, 3, 1])
        XCTAssertTrue(document.pdf.page(at: 2) === pdfPages[0])
        XCTAssertTrue(document.pdf.page(at: 0) === pdfPages[1])

        document.undoManager.undo()
        assertAligned(document)
        XCTAssertEqual(document.pages.map(\.id), ids)
        XCTAssertTrue(document.pdf.page(at: 0) === pdfPages[0])

        document.undoManager.redo()
        XCTAssertEqual(document.pages.map(\.id), [ids[1], ids[2], ids[0]])
    }

    @MainActor func testOnlyPageCannotBeDeleted() async throws {
        let document = try await openNote(pages: 1)
        let id = document.pageID(at: 0)
        document.deletePage(at: 0)
        assertAligned(document)
        XCTAssertEqual(document.pages.map(\.id), [id])
        XCTAssertFalse(document.undoManager.canUndo)
        document.deletePage(at: 5)
        XCTAssertEqual(document.pages.count, 1)
    }

    @MainActor func testImportPagesBringsTheirInk() async throws {
        let document = try await openNote(pages: 2)
        let source = TestSupport.pdf(pages: 2)
        let geometries = (0..<2).compactMap { source.page(at: $0) }.map { PageGeometry(page: $0) }
        guard let sourceData = source.dataRepresentation() else { return XCTFail("no source data") }
        let snapshot = NoteSnapshot(pdfData: sourceData, drawings: [drawing(strokes: 2), drawing(strokes: 1)],
                                    geometries: geometries, pageCount: 2, noteID: UUID().uuidString)
        let rendered = try NoteWriter.render(snapshot)
        guard let other = PDFDocument(data: rendered) else { return XCTFail("render") }
        let otherInk = other.page(at: 0)?.annotations.filter { $0.type == "Ink" }.count ?? 0
        XCTAssertEqual(otherInk, 2, "the other note carries its ink as annotations")

        document.importPages(from: other, at: 1)
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 4)
        XCTAssertEqual(document.pages[1].drawing.strokes.count, 2)
        XCTAssertEqual(document.pages[2].drawing.strokes.count, 1)
        XCTAssertEqual(document.pages[3].drawing.strokes.count, 0)
        for index in 1...2 {
            let ink = document.pdf.page(at: index)?.annotations.filter { $0.type == "Ink" } ?? []
            XCTAssertTrue(ink.isEmpty, "ink lives in the drawing, not in the displayed PDF")
        }
        XCTAssertEqual(other.pageCount, 2, "the other document is left as it was")
        XCTAssertEqual(other.page(at: 0)?.annotations.filter { $0.type == "Ink" }.count ?? 0, 2)

        document.undoManager.undo()
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 2)
    }

    @MainActor func testImagePages() async throws {
        let document = try await openNote(pages: 1)
        document.insertImagePages([photo(width: 3000, height: 2000), photo(width: 600, height: 900)], at: 0)
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 3)
        guard let wide = document.pdf.page(at: 0), let tall = document.pdf.page(at: 1) else { return XCTFail("no pages") }
        let wideBox = wide.bounds(for: .mediaBox), tallBox = tall.bounds(for: .mediaBox)
        XCTAssertEqual(wideBox.width / wideBox.height, 1.5, accuracy: 0.01)
        XCTAssertEqual(tallBox.width / tallBox.height, 600.0 / 900.0, accuracy: 0.01)
        XCTAssertLessThanOrEqual(wideBox.width, PaperSize.a4.points.width + 0.5)
        XCTAssertLessThanOrEqual(tallBox.height, PaperSize.a4.points.height + 0.5)
        let centre = TemplateTests.pixel(of: wide.thumbnail(of: CGSize(width: 150, height: 100), for: .mediaBox),
                                         at: CGPoint(x: 0.5, y: 0.5))
        XCTAssertGreaterThan(centre?.b ?? 0, 0.6, "the photo is drawn on its page")

        document.undoManager.undo()
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 1)

        guard let data = PageFactory.pdfData(from: [photo(width: 400, height: 300), photo(width: 300, height: 400)]),
              let note = PDFDocument(data: data) else { return XCTFail("no note from images") }
        XCTAssertEqual(note.pageCount, 2)
        XCTAssertNil(PageFactory.pdfData(from: []))
    }

    @MainActor func testThumbnailIncludesInk() {
        let pdf = TestSupport.pdf(pages: 1, rotation: 90)
        guard let page = pdf.page(at: 0) else { return XCTFail("no page") }
        let geometry = PageGeometry(page: page)
        let display = geometry.displaySize
        XCTAssertGreaterThan(display.width, display.height, "a quarter turn shows A4 landscape")
        let stroke = TestSupport.stroke([CGPoint(x: 0, y: display.height / 2), CGPoint(x: display.width, y: display.height / 2)],
                                        width: 40, color: .black)
        var image = UIImage()
        UITraitCollection(userInterfaceStyle: .dark).performAsCurrent {
            image = PageThumbnailer.thumbnail(page: page, drawing: PKDrawing(strokes: [stroke]), geometry: geometry, width: 200)
        }
        XCTAssertEqual(image.size.width, 200, accuracy: 0.5)
        XCTAssertEqual(image.size.height, 200 * display.height / display.width, accuracy: 0.5)
        guard let onInk = TemplateTests.pixel(of: image, at: CGPoint(x: 0.5, y: 0.5)),
              let offInk = TemplateTests.pixel(of: image, at: CGPoint(x: 0.5, y: 0.1)) else { return XCTFail("no pixels") }
        XCTAssertLessThan(onInk.r + onInk.g + onInk.b, 0.6, "black ink in the thumbnail, also in dark mode")
        XCTAssertGreaterThan(offInk.r + offInk.g + offInk.b, 2.4, "white paper elsewhere")
    }

    @MainActor func testWritingOnLastPageAppendsPage() async throws {
        let document = try await openNote(pages: 2)
        document.template = PaperTemplate(pattern: .dotted, color: .cream, size: .letter)
        document.appendPageIfLast(document.pageID(at: 0))
        XCTAssertEqual(document.pages.count, 2, "not the last page: nothing appended")

        document.appendPageIfLast(document.pageID(at: 1))
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 3)
        let added = document.pageID(at: 2)
        XCTAssertEqual(document.autoAddedPageIDs, [added])
        XCTAssertEqual(document.pdf.page(at: 2)?.bounds(for: .mediaBox).width ?? 0, PaperSize.letter.points.width, accuracy: 0.01)
    }

    @MainActor func testAppendedPageUndoneWithStroke() async throws {
        let document = try await openNote(pages: 1)
        let id = document.pageID(at: 0)
        // What the editor does in one event: the stroke's undo registration, then continuous paper.
        document.setDrawing(drawing(strokes: 1), forPageID: id, undoable: true)
        document.appendPageIfLast(id)
        XCTAssertEqual(document.pages.count, 2)

        document.undoManager.undo()
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 1, "one undo removes the page")
        XCTAssertTrue(document.drawing(forPageID: id).strokes.isEmpty, "and the stroke")

        document.undoManager.redo()
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 2)
        XCTAssertEqual(document.drawing(forPageID: id).strokes.count, 1)
    }

    @MainActor func testTrailingEmptyPageRemovedOnClose() async throws {
        let document = try await openNote(pages: 1)
        document.appendPageIfLast(document.pageID(at: 0))
        document.appendPageIfLast(document.pageID(at: 1))
        document.appendPageIfLast(document.pageID(at: 2))
        XCTAssertEqual(document.pages.count, 4)
        document.setDrawing(drawing(strokes: 1), forPageID: document.pageID(at: 1), undoable: false)
        // An auto page holding only an annotation is not empty either.
        let annotated = document.pageID(at: 2)
        document.pdf.page(at: 2)?.addAnnotation(PDFAnnotation(bounds: CGRect(x: 50, y: 50, width: 80, height: 40),
                                                              forType: .square, withProperties: nil))
        let empty = document.pageID(at: 3)

        document.dropTrailingEmptyAutoPages()
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 3)
        XCTAssertEqual(document.pages.last?.id, annotated)
        XCTAssertFalse(document.autoAddedPageIDs.contains(empty))

        // Through close, which a note's own save follows.
        document.pdf.page(at: 2)?.annotations.forEach { document.pdf.page(at: 2)?.removeAnnotation($0) }
        let url = document.url
        await document.close()
        XCTAssertEqual(document.pages.count, 2, "the emptied auto page goes too, the inked one stays")
        XCTAssertEqual(PDFDocument(url: url)?.pageCount, 2)
    }

    @MainActor func testUserAddedBlankPageKept() async throws {
        let document = try await openNote(pages: 1)
        document.addPage(at: 1)
        document.dropTrailingEmptyAutoPages()
        XCTAssertEqual(document.pages.count, 2, "a page added on purpose stays")

        // Auto page in the middle, the person's page last: nothing trailing is automatic.
        document.appendPageIfLast(document.pageID(at: 1))
        document.addPage(at: 3)
        document.dropTrailingEmptyAutoPages()
        assertAligned(document)
        XCTAssertEqual(document.pages.count, 4)

        // The only page is never removed, even an automatic one.
        let single = try await openNote(pages: 1)
        single.autoAddedPageIDs.insert(single.pageID(at: 0))
        single.dropTrailingEmptyAutoPages()
        XCTAssertEqual(single.pages.count, 1)
    }
}
