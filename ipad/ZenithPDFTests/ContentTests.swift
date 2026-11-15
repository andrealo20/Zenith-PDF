import PDFKit
import PencilKit
import UIKit
import XCTest
@testable import ZenithPDF

final class ContentTests: XCTestCase {
    // MARK: - Helpers

    /// An open note with an editor and the two content controllers the editor model would make.
    @MainActor private struct Fixture {
        let document: NoteDocument
        let editor: EditorViewController
        let textBoxes: TextBoxController
        let selection: SelectionController
        var page: PDFPage { document.pdf.page(at: 0) ?? PDFPage() }
    }

    @MainActor private func fixture(pages: Int = 1, name: String = #function) async throws -> Fixture {
        let folder = TestSupport.temporaryFolder(name)
        let url = TestSupport.writeNote(TestSupport.pdf(pages: pages), named: "Note", in: folder)
        let document = try await NoteDocument.open(url, libraryRoot: folder)
        // Each step of a test is its own undo group, as each would be its own event in the app.
        document.undoManager.groupsByEvent = false
        let editor = EditorViewController(document: document)
        let textBoxes = TextBoxController(editor: editor)
        let selection = SelectionController(editor: editor, textBoxes: textBoxes)
        return Fixture(document: document, editor: editor, textBoxes: textBoxes, selection: selection)
    }

    @MainActor private func step(_ document: NoteDocument, _ body: () -> Void) {
        document.undoManager.beginUndoGrouping()
        body()
        document.undoManager.endUndoGrouping()
    }

    @MainActor private func textBoxes(on page: PDFPage) -> [PDFAnnotation] {
        page.annotations.filter { TextBoxCodec.isTextBox($0) }
    }

    @MainActor private func pictures(on page: PDFPage) -> [ImageAnnotation] {
        page.annotations.compactMap { $0 as? ImageAnnotation }
    }

    @MainActor private func image(width: CGFloat, height: CGFloat, color: UIColor = .systemRed) -> UIImage {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = true
        return UIGraphicsImageRenderer(size: CGSize(width: width, height: height), format: format).image { context in
            color.setFill()
            context.fill(CGRect(x: 0, y: 0, width: width, height: height))
        }
    }

    @MainActor private func picture(at bounds: CGRect) -> ImageAnnotation {
        ImageAnnotation(bounds: bounds, imageData: image(width: 40, height: 20).pngData() ?? Data())
    }

    /// A horizontal stroke in display space, sampled every 10 pt so the B-spline runs along it.
    @MainActor private func line(y: CGFloat, width: CGFloat = 8) -> PKStroke {
        TestSupport.stroke(stride(from: CGFloat(50), through: 300, by: 10).map { CGPoint(x: $0, y: y) }, width: width)
    }

    private func assertRect(_ rect: CGRect, _ expected: CGRect, accuracy: CGFloat = 0.01,
                            file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertEqual(rect.minX, expected.minX, accuracy: accuracy, "x", file: file, line: line)
        XCTAssertEqual(rect.minY, expected.minY, accuracy: accuracy, "y", file: file, line: line)
        XCTAssertEqual(rect.width, expected.width, accuracy: accuracy, "width", file: file, line: line)
        XCTAssertEqual(rect.height, expected.height, accuracy: accuracy, "height", file: file, line: line)
    }

    // MARK: - Text boxes

    @MainActor func testCreateTextBoxAtTap() async throws {
        let f = try await fixture()
        let page = f.page
        var handled = false
        step(f.document) { handled = f.textBoxes.handleTap(page: page, pdfPoint: CGPoint(x: 100, y: 700)) }
        XCTAssertTrue(handled)
        let textView = try XCTUnwrap(f.textBoxes.textView)
        XCTAssertTrue(textView.superview === f.editor.overlayHost, "the editor sits in overlayHost")
        XCTAssertNotNil(f.textBoxes.editing)
        XCTAssertTrue(textBoxes(on: page).isEmpty, "the box goes on the page when it has text")

        textView.text = "Hello"
        step(f.document) { f.textBoxes.endEditing() }
        XCTAssertNil(f.textBoxes.editing)
        XCTAssertNil(textView.superview)
        let boxes = textBoxes(on: page)
        XCTAssertEqual(boxes.count, 1)
        let box = try XCTUnwrap(boxes.first)
        XCTAssertEqual(box.contents, "Hello")
        XCTAssertEqual(box.bounds.minX, 100, accuracy: 0.01)
        XCTAssertEqual(box.bounds.maxY, 700, accuracy: 0.01, "the tap is the box's top-left corner")
        XCTAssertEqual(box.bounds.width, 240, accuracy: 0.01)
        XCTAssertGreaterThan(box.bounds.height, 8)
        XCTAssertTrue(f.document.isDirty)

        f.document.undoManager.undo()
        XCTAssertTrue(textBoxes(on: page).isEmpty)
        f.document.undoManager.redo()
        XCTAssertEqual(textBoxes(on: page).first?.contents, "Hello")
    }

    @MainActor func testTapNearRightEdgeKeepsBoxOnPage() async throws {
        let f = try await fixture()
        let page = f.page
        let box = PageGeometry(page: page).box
        step(f.document) { _ = f.textBoxes.handleTap(page: page, pdfPoint: CGPoint(x: box.maxX - 20, y: 400)) }
        f.textBoxes.textView?.text = "Edge"
        step(f.document) { f.textBoxes.endEditing() }
        let made = try XCTUnwrap(textBoxes(on: page).first)
        XCTAssertLessThanOrEqual(made.bounds.maxX, box.maxX + 0.01)
    }

    @MainActor func testEditTextBoxUpdatesAnnotationAndUndo() async throws {
        let f = try await fixture()
        let page = f.page
        var courier = TextBoxStyle()
        courier.font = .courier
        courier.size = 18
        let box = TextBoxCodec.makeAnnotation(text: "One", style: courier,
                                              bounds: CGRect(x: 100, y: 500, width: 200, height: 40))
        page.addAnnotation(box)
        var adopted: TextBoxStyle?
        f.textBoxes.onStyleChange = { adopted = $0 }

        step(f.document) { XCTAssertTrue(f.textBoxes.handleTap(page: page, pdfPoint: CGPoint(x: 150, y: 520))) }
        XCTAssertTrue(f.textBoxes.editing === box, "a tap on a box edits it instead of making a new one")
        XCTAssertFalse(box.shouldDisplay, "PDFKit does not draw the text under the editor")
        XCTAssertEqual(f.textBoxes.textView?.text, "One")
        XCTAssertEqual(f.textBoxes.style.font, .courier, "editing takes over the box's style")
        XCTAssertEqual(adopted?.font, .courier)
        XCTAssertEqual(f.textBoxes.style.size, 18, accuracy: 0.01)

        f.textBoxes.textView?.text = "One two"
        step(f.document) { f.textBoxes.endEditing() }
        XCTAssertTrue(box.shouldDisplay)
        XCTAssertEqual(box.contents, "One two")
        XCTAssertEqual(textBoxes(on: page).count, 1)
        XCTAssertEqual(TextBoxCodec.style(of: box).font, .courier, "the box keeps its own font")

        f.document.undoManager.undo()
        XCTAssertEqual(box.contents, "One")
        f.document.undoManager.redo()
        XCTAssertEqual(box.contents, "One two")
    }

    @MainActor func testEditExistingNeverCreates() async throws {
        let f = try await fixture()
        let page = f.page
        XCTAssertFalse(f.textBoxes.editExisting(page: page, pdfPoint: CGPoint(x: 300, y: 300)))
        XCTAssertNil(f.textBoxes.editing)
        XCTAssertNil(f.textBoxes.textView)
        XCTAssertTrue(page.annotations.isEmpty)

        let box = TextBoxCodec.makeAnnotation(text: "Here", style: TextBoxStyle(),
                                              bounds: CGRect(x: 100, y: 100, width: 120, height: 30))
        page.addAnnotation(box)
        XCTAssertTrue(f.textBoxes.editExisting(page: page, pdfPoint: CGPoint(x: 110, y: 110)))
        XCTAssertTrue(f.textBoxes.editing === box)
        step(f.document) { f.textBoxes.endEditing() }
        XCTAssertEqual(textBoxes(on: page).count, 1)
        XCTAssertFalse(f.document.undoManager.canUndo, "nothing changed, nothing to undo")
    }

    @MainActor func testEmptyNewBoxRemoved() async throws {
        let f = try await fixture()
        let page = f.page
        step(f.document) { _ = f.textBoxes.handleTap(page: page, pdfPoint: CGPoint(x: 200, y: 400)) }
        XCTAssertNotNil(f.textBoxes.editing)
        f.textBoxes.textView?.text = "  \n "
        step(f.document) { f.textBoxes.endEditing() }
        XCTAssertNil(f.textBoxes.editing)
        XCTAssertTrue(textBoxes(on: page).isEmpty)
        XCTAssertFalse(f.document.undoManager.canUndo)
        XCTAssertFalse(f.document.isDirty)

        // A tap away from a box being edited only finishes it: no second box appears.
        step(f.document) { _ = f.textBoxes.handleTap(page: page, pdfPoint: CGPoint(x: 200, y: 400)) }
        f.textBoxes.textView?.text = "Kept"
        step(f.document) { XCTAssertTrue(f.textBoxes.handleTap(page: page, pdfPoint: CGPoint(x: 200, y: 150))) }
        XCTAssertNil(f.textBoxes.editing)
        XCTAssertEqual(textBoxes(on: page).map { $0.contents ?? "" }, ["Kept"])
    }

    @MainActor func testStyleApplied() async throws {
        let f = try await fixture()
        let page = f.page
        var style = TextBoxStyle()
        style.font = .times
        style.size = 20
        style.color = RGB(r: 0.8, g: 0.1, b: 0.2)
        style.bold = true
        style.alignment = 1
        step(f.document) { _ = f.textBoxes.handleTap(page: page, pdfPoint: CGPoint(x: 80, y: 600)) }
        // The style bar changes the box being edited.
        f.textBoxes.style = style
        let textView = try XCTUnwrap(f.textBoxes.textView)
        XCTAssertEqual(textView.font?.fontName, style.uiFont.fontName)
        XCTAssertEqual(textView.textAlignment, .center)
        textView.text = "Styled"
        step(f.document) { f.textBoxes.endEditing() }

        let box = try XCTUnwrap(textBoxes(on: page).first)
        let read = TextBoxCodec.style(of: box)
        XCTAssertEqual(read.font, .times)
        XCTAssertTrue(read.bold)
        XCTAssertFalse(read.italic)
        XCTAssertEqual(read.size, 20, accuracy: 0.01)
        XCTAssertEqual(read.alignment, 1)
        XCTAssertTrue(read.color.isClose(to: style.color, tolerance: 0.01))
        XCTAssertEqual(box.value(forAnnotationKey: ZenithKey.defaultStyle) as? String, TextBoxCodec.styleString(style))

        await f.document.save()
        let written = PDFRaw.annotations(inFile: f.document.url, pageIndex: 0).filter { $0.subtype == "FreeText" }
        XCTAssertEqual(written.count, 1)
        XCTAssertEqual(written.first?.contents, "Styled")
        XCTAssertEqual(written.first?.defaultStyle, TextBoxCodec.styleString(style), "/DS in the desktop's format")
    }

    // MARK: - Selection

    @MainActor func testMoveAnnotationUndo() async throws {
        let f = try await fixture()
        let page = f.page
        let start = CGRect(x: 100, y: 100, width: 80, height: 40)
        let photo = picture(at: start)
        page.addAnnotation(photo)

        XCTAssertFalse(f.selection.handleTap(page: page, pdfPoint: CGPoint(x: 400, y: 600)), "nothing there")
        XCTAssertTrue(f.selection.handleTap(page: page, pdfPoint: CGPoint(x: 140, y: 120)))
        XCTAssertTrue(f.selection.selected === photo)

        let moved = start.offsetBy(dx: 60, dy: 30)
        step(f.document) {
            f.selection.adjust(to: start.offsetBy(dx: 20, dy: 10))
            f.selection.adjust(to: moved)
            f.selection.finishAdjusting(actionName: "Move")
        }
        assertRect(photo.bounds, moved)
        XCTAssertTrue(f.document.isDirty)

        f.document.undoManager.undo()
        assertRect(photo.bounds, start)
        XCTAssertFalse(f.document.undoManager.canUndo, "the whole drag is one undo step")
        XCTAssertTrue(f.selection.selected === photo, "the selection follows the undo")
        f.document.undoManager.redo()
        assertRect(photo.bounds, moved)
    }

    @MainActor func testResizeKeepsPictureAspect() async throws {
        let f = try await fixture()
        let page = f.page
        let start = CGRect(x: 100, y: 100, width: 100, height: 50)
        let photo = picture(at: start)
        page.addAnnotation(photo)
        XCTAssertTrue(f.selection.handleTap(page: page, pdfPoint: CGPoint(x: 150, y: 125)))

        step(f.document) {
            // Dragged mostly sideways: the picture grows to the larger factor, 3, in both directions.
            f.selection.resizeSelected(fixed: CGPoint(x: 100, y: 100), moving: CGPoint(x: 400, y: 160))
            f.selection.finishAdjusting(actionName: "Resize")
        }
        assertRect(photo.bounds, CGRect(x: 100, y: 100, width: 300, height: 150))
        XCTAssertEqual(photo.bounds.width / photo.bounds.height, 2, accuracy: 0.001)
        f.document.undoManager.undo()
        assertRect(photo.bounds, start)

        // Dragged past the fixed corner: the box flips to that side.
        step(f.document) {
            f.selection.resizeSelected(fixed: CGPoint(x: 200, y: 150), moving: CGPoint(x: 150, y: 140))
            f.selection.finishAdjusting(actionName: "Resize")
        }
        assertRect(photo.bounds, CGRect(x: 150, y: 125, width: 50, height: 25))

        // A text box resizes freely.
        let box = TextBoxCodec.makeAnnotation(text: "Free", style: TextBoxStyle(),
                                              bounds: CGRect(x: 300, y: 500, width: 100, height: 30))
        page.addAnnotation(box)
        XCTAssertTrue(f.selection.handleTap(page: page, pdfPoint: CGPoint(x: 320, y: 510)))
        XCTAssertTrue(f.selection.selected === box)
        step(f.document) {
            f.selection.resizeSelected(fixed: CGPoint(x: 300, y: 500), moving: CGPoint(x: 500, y: 580))
            f.selection.finishAdjusting(actionName: "Resize")
        }
        assertRect(box.bounds, CGRect(x: 300, y: 500, width: 200, height: 80))

        // No side ends up below the minimum.
        let tiny = SelectionController.resized(start, fixed: CGPoint(x: 100, y: 100), moving: CGPoint(x: 101, y: 101),
                                               keepAspect: true, minimum: 8)
        XCTAssertGreaterThanOrEqual(min(tiny.width, tiny.height), 8 - 0.001)
        XCTAssertEqual(tiny.width / tiny.height, 2, accuracy: 0.001)
    }

    @MainActor func testDeleteAndDuplicateSelection() async throws {
        let f = try await fixture()
        let page = f.page
        let original = picture(at: CGRect(x: 100, y: 300, width: 80, height: 40))
        page.addAnnotation(original)
        XCTAssertTrue(f.selection.handleTap(page: page, pdfPoint: CGPoint(x: 120, y: 320)))

        step(f.document) { f.selection.duplicateSelected() }
        let all = pictures(on: page)
        XCTAssertEqual(all.count, 2)
        let copy = try XCTUnwrap(all.first { $0 !== original })
        XCTAssertTrue(f.selection.selected === copy, "the copy is selected")
        assertRect(copy.bounds, original.bounds.offsetBy(dx: 12, dy: -12))
        XCTAssertEqual(copy.imageData, original.imageData)

        f.document.undoManager.undo()
        XCTAssertEqual(pictures(on: page).count, 1)
        XCTAssertNil(f.selection.selected, "an undo that removes the selected object clears the selection")
        f.document.undoManager.redo()
        XCTAssertEqual(pictures(on: page).count, 2)

        XCTAssertTrue(f.selection.handleTap(page: page, pdfPoint: CGPoint(x: 120, y: 320)))
        step(f.document) { f.selection.deleteSelected() }
        XCTAssertNil(f.selection.selected)
        XCTAssertEqual(pictures(on: page).count, 1)
        f.document.undoManager.undo()
        XCTAssertEqual(pictures(on: page).count, 2)

        // Text boxes duplicate with their text and style.
        var style = TextBoxStyle()
        style.font = .times
        style.italic = true
        let box = TextBoxCodec.makeAnnotation(text: "Twice", style: style, bounds: CGRect(x: 300, y: 600, width: 120, height: 30))
        page.addAnnotation(box)
        XCTAssertTrue(f.selection.handleTap(page: page, pdfPoint: CGPoint(x: 310, y: 610)))
        step(f.document) { f.selection.duplicateSelected() }
        let boxes = textBoxes(on: page)
        XCTAssertEqual(boxes.count, 2)
        let twin = try XCTUnwrap(boxes.first { $0 !== box })
        XCTAssertEqual(twin.contents, "Twice")
        XCTAssertEqual(TextBoxCodec.style(of: twin).font, .times)
        XCTAssertTrue(TextBoxCodec.style(of: twin).italic)
    }

    // MARK: - Pictures

    @MainActor func testInsertImageCentredAndScaled() async throws {
        let f = try await fixture()
        let page = f.page
        let box = PageGeometry(page: page).box

        ImageInsertion.insert(image(width: 2000, height: 1000), into: f.editor)
        let large = try XCTUnwrap(pictures(on: page).first)
        XCTAssertEqual(large.bounds.width, box.width * 0.6, accuracy: 0.01, "at most 60% of the page width")
        XCTAssertEqual(large.bounds.height, box.width * 0.3, accuracy: 0.01, "aspect kept")
        XCTAssertEqual(large.bounds.midX, box.midX, accuracy: 0.01)
        XCTAssertEqual(large.bounds.midY, box.midY, accuracy: 0.01)
        XCTAssertFalse(large.imageData.isEmpty)
        XCTAssertNotNil(large.image)

        ImageInsertion.insert(image(width: 40, height: 30), into: f.editor)
        let small = try XCTUnwrap(pictures(on: page).first { $0 !== large })
        XCTAssertEqual(small.bounds.width, 40, accuracy: 0.01, "a small picture is not blown up")
        XCTAssertEqual(small.bounds.height, 30, accuracy: 0.01)
        XCTAssertEqual(small.bounds.midX, box.midX, accuracy: 0.01)
    }

    @MainActor func testInsertImageUndo() async throws {
        let f = try await fixture()
        let page = f.page
        step(f.document) { ImageInsertion.insert(image(width: 300, height: 200), into: f.editor) }
        XCTAssertEqual(pictures(on: page).count, 1)
        XCTAssertTrue(f.document.isDirty)
        f.document.undoManager.undo()
        XCTAssertTrue(pictures(on: page).isEmpty)
        f.document.undoManager.redo()
        XCTAssertEqual(pictures(on: page).count, 1)

        await f.document.save()
        let stamps = PDFRaw.annotations(inFile: f.document.url, pageIndex: 0).filter { $0.subtype == "Stamp" }
        XCTAssertEqual(stamps.count, 1)
        XCTAssertNotNil(stamps.first?.imageData, "the picture's bytes ride along in /ZenithImageData")
    }

    // MARK: - Sharing

    @MainActor func testFlattenedCopyHasNoInkAnnotations() async throws {
        let f = try await fixture(pages: 2)
        f.document.setDrawing(PKDrawing(strokes: [line(y: 100), line(y: 200)]), forPageID: f.document.pageID(at: 0),
                              undoable: false)
        f.document.setDrawing(PKDrawing(strokes: [line(y: 300)]), forPageID: f.document.pageID(at: 1), undoable: false)

        let url = try await ShareController.fileToShare(for: f.document, flattened: true)
        XCTAssertNotEqual(url.standardizedFileURL, f.document.url.standardizedFileURL)
        XCTAssertEqual(url.lastPathComponent, "Note.pdf")
        XCTAssertTrue(url.path.hasPrefix(FileManager.default.temporaryDirectory.resolvingSymlinksInPath().path)
                      || url.path.hasPrefix(FileManager.default.temporaryDirectory.path))
        XCTAssertEqual(PDFRaw.annotations(inFile: f.document.url, pageIndex: 0).filter { $0.subtype == "Ink" }.count, 2,
                       "the note itself keeps editable ink")

        let flat = try XCTUnwrap(PDFDocument(url: url))
        XCTAssertEqual(flat.pageCount, 2)
        for index in 0..<2 {
            XCTAssertTrue(PDFRaw.annotations(inFile: url, pageIndex: index).filter { $0.subtype == "Ink" }.isEmpty,
                          "page \(index) has no ink annotations")
        }
        // The ink is drawn into the page rather than dropped.
        let page = try XCTUnwrap(flat.page(at: 0))
        let size = PageGeometry(page: page).displaySize
        let render = page.thumbnail(of: size, for: .mediaBox)
        let inked = try XCTUnwrap(TemplateTests.pixel(of: render, at: CGPoint(x: 175 / size.width, y: 100 / size.height)))
        XCTAssertLessThan(inked.r + inked.g + inked.b, 1.5, "the stroke is in the page content")
    }

    @MainActor func testShareSavesFirst() async throws {
        let f = try await fixture()
        f.document.setDrawing(PKDrawing(strokes: [line(y: 150)]), forPageID: f.document.pageID(at: 0), undoable: false)
        XCTAssertTrue(f.document.isDirty)
        XCTAssertTrue(PDFRaw.annotations(inFile: f.document.url, pageIndex: 0).isEmpty)

        let url = try await ShareController.fileToShare(for: f.document, flattened: false)
        XCTAssertEqual(url, f.document.url, "the note itself, with editable ink")
        XCTAssertFalse(f.document.isDirty, "shared only after saving")
        XCTAssertEqual(PDFRaw.annotations(inFile: url, pageIndex: 0).filter { $0.subtype == "Ink" }.count, 1)
    }
}
