import Foundation
import Observation
import PDFKit
import PencilKit
import UIKit

/// The open note: model, dirty state, undo, save.
@MainActor @Observable final class NoteDocument {
    private(set) var url: URL                     // changes after a conflict (the copy) or a rename
    let libraryRoot: URL?                         // nil for a file opened in place from outside the library
    let pdf: PDFDocument                          // the displayed document (no ink annotations inside)
    private(set) var pages: [NotePage]
    var template: PaperTemplate                   // meta.json; this class is its only writer
    var lastPage: Int                             // meta.json
    let noteID: String                            // /ZenithNoteID; a new UUID string when the file had none
    let undoManager: UndoManager                  // the note's only undo stack (section 3.5)
    private(set) var isDirty: Bool = false
    private(set) var isSaving: Bool = false
    private(set) var isReadOnly: Bool = false     // locked PDF or no write access
    var lastError: ZenithError? = nil
    private(set) var revision: Int = 0            // +1 on every change
    var autoAddedPageIDs: Set<UUID> = []          // pages continuous paper appended in this session (WP4 keeps it)
    var title: String { url.deletingPathExtension().lastPathComponent }
    var onDrawingReplaced: ((UUID) -> Void)? = nil     // the editor reloads that page's canvas
    var onPagesChanged: (() -> Void)? = nil            // after insert, remove, move
    var onSaved: (() -> Void)? = nil                   // indexer, thumbnails
    /// Starts a background task and returns the function that ends it. Default: UIApplication.shared.beginBackgroundTask
    /// whose expiration handler ends the task (the note stays dirty). Tests replace it to see that saves hold one.
    var beginBackgroundTask: (_ name: String) -> (() -> Void) = { _ in {} }

    private init(url: URL, libraryRoot: URL?, pdf: PDFDocument, pages: [NotePage], noteID: String) {
        self.url = url
        self.libraryRoot = libraryRoot
        self.pdf = pdf
        self.pages = pages
        self.template = .standard
        self.lastPage = 0
        self.noteID = noteID
        self.undoManager = UndoManager()
    }

    static func open(_ url: URL, libraryRoot: URL?) async throws -> NoteDocument {
        guard let pdf = PDFDocument(url: url) else { throw ZenithError.cannotOpen(url) }
        let pages = (0..<pdf.pageCount).map { _ in NotePage() }
        return NoteDocument(url: url, libraryRoot: libraryRoot, pdf: pdf, pages: pages, noteID: UUID().uuidString)
    }

    func index(of page: PDFPage) -> Int? {
        let index = pdf.index(for: page)
        return pages.indices.contains(index) ? index : nil
    }

    func index(ofPageID id: UUID) -> Int? {
        pages.firstIndex { $0.id == id }
    }

    func pageID(at index: Int) -> UUID {
        pages.indices.contains(index) ? pages[index].id : UUID()
    }

    func geometry(at index: Int) -> PageGeometry {
        if let page = pdf.page(at: index) { return PageGeometry(page: page) }
        return PageGeometry(box: CGRect(origin: .zero, size: PaperSize.a4.points), rotation: 0)
    }

    func drawing(forPageID id: UUID) -> PKDrawing {
        pages.first { $0.id == id }?.drawing ?? PKDrawing()
    }

    /// Replaces a page's ink. undoable: registers one undo that puts the previous drawing back (its redo comes for
    /// free, since the undo calls setDrawing again) and calls onDrawingReplaced; used for every change the person makes
    /// and every code replacement. false only while loading and for demo content.
    func setDrawing(_ drawing: PKDrawing, forPageID id: UUID, undoable: Bool, actionName: String? = nil) {
    }

    /// Text boxes, pictures, pages: dirty + revision + cached bytes stale + scheduleSave.
    func markChanged() {
    }

    /// The live document's bytes: the cache, refreshed with pdf.dataRepresentation() only when markChanged ran since.
    func currentPDFData() -> Data? {
        pdf.dataRepresentation()
    }

    /// 5 s after the last change, never sooner than 10 s after the previous save began.
    func scheduleSave() {
    }

    /// Section 4.7; no-op when clean; one at a time; a change during a save saves again after.
    func save() async {
    }

    /// dropTrailingEmptyAutoPages, save, stop security-scoped access, clear undo.
    func close() async {
    }

    // Primitives for WP4 (no undo registration here):
    func insertPage(_ page: PDFPage, notePage: NotePage, at index: Int) {
    }

    func removePage(at index: Int) -> (PDFPage, NotePage) {
        let notePage = pages.indices.contains(index) ? pages[index] : NotePage()
        return (pdf.page(at: index) ?? PDFPage(), notePage)
    }

    func movePage(from source: Int, to destination: Int) {
    }
}
