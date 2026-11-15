import PDFKit
import UIKit

// Undoable page operations, continuous paper. The undoable ones call markChanged and onPagesChanged.
extension NoteDocument {
    /// nil: the note's template.
    func addPage(at index: Int, template: PaperTemplate? = nil) {
    }

    /// A fresh page, never PDFPage.copy() (section 9): PDFDocument(data: currentPDFData()), NoteReader.extract on it
    /// (pictures become new ImageAnnotation objects), that page removed from the temporary document and inserted
    /// here, with a copy of the drawing's data.
    func duplicatePage(at index: Int) {
    }

    /// Refused when it is the only page.
    func deletePage(at index: Int) {
    }

    func reorderPage(from source: Int, to destination: Int) {
    }

    /// Other's ink comes along as strokes (NoteReader.extract on a copy).
    func importPages(from other: PDFDocument, at index: Int) {
    }

    func insertImagePages(_ images: [UIImage], at index: Int) {
    }

    /// Continuous paper: when `pageID` is the last page, appends a page of the note's template and adds it to
    /// autoAddedPageIDs. Undoable; the editor calls it in the same event as the stroke's own undo registration, so
    /// both land in one undo group and one Undo removes the stroke and the page.
    func appendPageIfLast(_ pageID: UUID) {
    }

    /// Without undo: removes trailing pages that are in autoAddedPageIDs and hold no ink and no annotations.
    /// A blank page the person added on purpose is never removed.
    func dropTrailingEmptyAutoPages() {
    }
}
