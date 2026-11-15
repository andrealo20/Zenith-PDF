import PDFKit
import PencilKit
import UIKit

/// A page taken out of the note (or about to go in), with the index it had (or gets).
private struct PlacedPage {
    let index: Int
    let page: PDFPage
    let notePage: NotePage
}

// Undoable page operations, continuous paper. The undoable ones call markChanged and onPagesChanged.
extension NoteDocument {
    /// nil: the note's template.
    func addPage(at index: Int, template: PaperTemplate? = nil) {
        let page = TemplateRenderer.page(for: template ?? self.template)
        let at = min(max(index, 0), pages.count)
        placePages([PlacedPage(index: at, page: page, notePage: NotePage())], actionName: tr("Add Page"))
    }

    /// A fresh page, never PDFPage.copy() (section 9): PDFDocument(data: currentPDFData()), NoteReader.extract on it
    /// (pictures become new ImageAnnotation objects), that page removed from the temporary document and inserted
    /// here, with a copy of the drawing's data.
    func duplicatePage(at index: Int) {
        guard pages.indices.contains(index), let data = currentPDFData(), let copy = PDFDocument(data: data),
              copy.pageCount == pages.count else { return }
        _ = NoteReader.extract(from: copy)
        guard let page = copy.page(at: index) else { return }
        copy.removePage(at: index)
        let original = pages[index].drawing
        let drawing = (try? PKDrawing(data: original.dataRepresentation())) ?? original
        placePages([PlacedPage(index: index + 1, page: page, notePage: NotePage(drawing: drawing))],
              actionName: tr("Duplicate Page"))
    }

    /// Refused when it is the only page.
    func deletePage(at index: Int) {
        guard pages.count > 1, pages.indices.contains(index) else { return }
        takePages([pages[index].id], actionName: tr("Delete Page"))
    }

    /// `destination` is the index the page has once moved.
    func reorderPage(from source: Int, to destination: Int) {
        guard pages.indices.contains(source), pages.indices.contains(destination), source != destination else { return }
        let id = pages[source].id
        let (page, notePage) = removePage(at: source)
        insertPage(page, notePage: notePage, at: destination)
        registerPageUndo(actionName: tr("Move Page")) { document in
            guard let current = document.index(ofPageID: id) else { return }
            document.reorderPage(from: current, to: source)
        }
        pagesDidChangeByEdit()
    }

    /// Other's ink comes along as strokes (NoteReader.extract on a copy).
    func importPages(from other: PDFDocument, at index: Int) {
        guard let data = other.dataRepresentation(), let copy = PDFDocument(data: data), copy.pageCount > 0 else { return }
        let notePages = NoteReader.extract(from: copy)
        let pdfPages = (0..<copy.pageCount).compactMap { copy.page(at: $0) }
        for position in stride(from: copy.pageCount - 1, through: 0, by: -1) {
            copy.removePage(at: position)
        }
        let at = min(max(index, 0), pages.count)
        let placed = pdfPages.enumerated().map { offset, page in
            PlacedPage(index: at + offset, page: page,
                       notePage: notePages.indices.contains(offset) ? NotePage(drawing: notePages[offset].drawing) : NotePage())
        }
        placePages(placed, actionName: tr("Pages from PDF"))
    }

    func insertImagePages(_ images: [UIImage], at index: Int) {
        let at = min(max(index, 0), pages.count)
        let placed = images.compactMap { PageFactory.page(from: $0) }.enumerated().map { offset, page in
            PlacedPage(index: at + offset, page: page, notePage: NotePage())
        }
        placePages(placed, actionName: tr("Photo as Page"))
    }

    /// Continuous paper: when `pageID` is the last page, appends a page of the note's template and adds it to
    /// autoAddedPageIDs. Undoable; the editor calls it in the same event as the stroke's own undo registration, so
    /// both land in one undo group and one Undo removes the stroke and the page.
    func appendPageIfLast(_ pageID: UUID) {
        guard pages.last?.id == pageID else { return }
        let notePage = NotePage()
        autoAddedPageIDs.insert(notePage.id)
        // No action name: the group is named after the stroke that caused it.
        placePages([PlacedPage(index: pages.count, page: TemplateRenderer.page(for: template), notePage: notePage)],
                   actionName: nil)
    }

    /// Without undo: removes trailing pages that are in autoAddedPageIDs and hold no ink and no annotations.
    /// A blank page the person added on purpose is never removed.
    func dropTrailingEmptyAutoPages() {
        var removed = false
        while pages.count > 1, let last = pages.last, autoAddedPageIDs.contains(last.id), last.drawing.strokes.isEmpty,
              let page = pdf.page(at: pages.count - 1), page.annotations.isEmpty {
            _ = removePage(at: pages.count - 1)
            autoAddedPageIDs.remove(last.id)
            removed = true
        }
        if removed { pagesDidChangeByEdit() }
    }

    // MARK: - Undoable building blocks

    /// Inserts the pages at their indices (ascending) and registers the undo that takes them out again.
    private func placePages(_ placed: [PlacedPage], actionName: String?) {
        guard !placed.isEmpty else { return }
        for item in placed.sorted(by: { $0.index < $1.index }) {
            insertPage(item.page, notePage: item.notePage, at: min(max(item.index, 0), pages.count))
        }
        let ids = placed.map { $0.notePage.id }
        registerPageUndo(actionName: actionName) { document in
            document.takePages(ids, actionName: actionName)
        }
        pagesDidChangeByEdit()
    }

    /// Removes the pages with these ids and registers the undo that puts them back where they were.
    /// Pages are found by id, so an undo step stays right however indices shifted; a page already gone is skipped.
    private func takePages(_ ids: [UUID], actionName: String?) {
        let indices = ids.compactMap { index(ofPageID: $0) }.sorted(by: >)
        guard !indices.isEmpty else { return }
        var removed: [PlacedPage] = []
        for index in indices {
            let (page, notePage) = removePage(at: index)
            removed.append(PlacedPage(index: index, page: page, notePage: notePage))
        }
        registerPageUndo(actionName: actionName) { document in
            document.placePages(removed, actionName: actionName)
        }
        pagesDidChangeByEdit()
    }

    private func registerPageUndo(actionName: String?, _ action: @escaping @MainActor (NoteDocument) -> Void) {
        undoManager.registerUndo(withTarget: self) { document in
            // The SDK may declare the handler @Sendable; undo always runs on the main thread.
            MainActor.assumeIsolated { action(document) }
        }
        if let actionName { undoManager.setActionName(actionName) }
    }

    private func pagesDidChangeByEdit() {
        markChanged()
        onPagesChanged?()
    }
}
