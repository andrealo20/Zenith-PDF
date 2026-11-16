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

    private static let idleDelay: Double = 5
    private static let minimumGap: TimeInterval = 10

    @ObservationIgnored private var known: SavedFile?
    @ObservationIgnored private var cachedData: Data? = nil
    @ObservationIgnored private var cacheIsStale = true
    @ObservationIgnored private var savedRevision = 0
    @ObservationIgnored private var saveAgain = false
    @ObservationIgnored private var lastSaveBegan: Date? = nil
    @ObservationIgnored private var wroteThisSession = false
    @ObservationIgnored private var pendingSave: Task<Void, Never>? = nil
    @ObservationIgnored private var saveDebouncer: Debouncer
    @ObservationIgnored private var scopedURL: URL? = nil

    private init(url: URL, libraryRoot: URL?, pdf: PDFDocument, pages: [NotePage], noteID: String) {
        self.url = url
        self.libraryRoot = libraryRoot
        self.pdf = pdf
        self.pages = pages
        self.template = .standard
        self.lastPage = 0
        self.noteID = noteID
        self.undoManager = UndoManager()
        self.saveDebouncer = Debouncer(seconds: NoteDocument.idleDelay)
        self.beginBackgroundTask = { name in
            let app = UIApplication.shared
            let task = NoteSaveTask()
            task.id = app.beginBackgroundTask(withName: name) {
                task.end(app)
            }
            return { task.end(app) }
        }
    }

    static func open(_ url: URL, libraryRoot: URL?) async throws -> NoteDocument {
        // A file from outside the library is reached through its own security scope until close.
        let scoped = libraryRoot == nil && url.startAccessingSecurityScopedResource()
        do {
            let data = try await Task.detached(priority: .userInitiated) { try FileStore.read(url) }.value
            guard let pdf = PDFDocument(data: data) else { throw ZenithError.invalidPDF }
            let attributes = pdf.documentAttributes ?? [:]
            let written = (attributes[ZenithKey.noteID] as? String) ?? (attributes[ZenithKey.noteID.rawValue] as? String)
                ?? pdf.documentRef.flatMap { PDFRaw.documentInfo(of: $0)[ZenithKey.Raw.noteID] }
            var noteID = written.flatMap { $0.isEmpty ? nil : $0 } ?? UUID().uuidString
            let sidecar = SidecarLayout.folder(forNote: url, libraryRoot: libraryRoot)
            if written != nil && !FileStore.exists(sidecar) {
                let adopted = try? MetadataStore.adoptOrphanedSidecar(forNote: url, noteID: noteID, libraryRoot: libraryRoot)
                // Nothing to adopt for a note that has an id: a copy of a note that still exists. It gets its own id.
                if adopted == nil { noteID = UUID().uuidString }
            }
            let metadata = MetadataStore.load(forNote: url, libraryRoot: libraryRoot)
            // A duplicate's meta.json gets a new id while its PDF still carries the original's; the sidecar wins,
            // and the next save writes it into the PDF.
            if let sidecarID = metadata.noteID, !sidecarID.isEmpty, sidecarID != noteID, FileStore.exists(sidecar) {
                noteID = sidecarID
            }
            let pages = NoteReader.extract(from: pdf)
            let document = NoteDocument(url: url, libraryRoot: libraryRoot, pdf: pdf, pages: pages, noteID: noteID)
            document.template = metadata.template
            document.lastPage = min(max(metadata.lastPage, 0), max(pages.count - 1, 0))
            document.known = SavedFile(url: url, modified: FileStore.modificationDate(url) ?? Date(),
                                       hash: FileStore.sha256(data))
            document.scopedURL = scoped ? url : nil
            if pdf.isLocked {
                document.isReadOnly = true
                document.lastError = .locked(url)
            } else if FileStore.exists(url) && !FileManager.default.isWritableFile(atPath: url.path) {
                document.isReadOnly = true
            }
            return document
        } catch {
            if scoped { url.stopAccessingSecurityScopedResource() }
            if let error = error as? ZenithError { throw error }
            throw ZenithError.cannotOpen(url)
        }
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
        guard let index = index(ofPageID: id) else { return }
        let old = pages[index].drawing
        pages[index].drawing = drawing
        if undoable {
            undoManager.registerUndo(withTarget: self) { document in
                document.setDrawing(old, forPageID: id, undoable: true, actionName: actionName)
            }
            if let actionName { undoManager.setActionName(actionName) }
        }
        onDrawingReplaced?(id)
        noteChange()
    }

    /// Text boxes, pictures, pages: dirty + revision + cached bytes stale + scheduleSave.
    func markChanged() {
        cacheIsStale = true
        noteChange()
    }

    /// The live document's bytes: the cache, refreshed with pdf.dataRepresentation() only when markChanged ran since.
    func currentPDFData() -> Data? {
        if cacheIsStale || cachedData == nil {
            cachedData = pdf.dataRepresentation()
            cacheIsStale = cachedData == nil
        }
        return cachedData
    }

    /// 5 s after the last change, never sooner than 10 s after the previous save began.
    func scheduleSave() {
        guard !isReadOnly else { return }
        saveDebouncer.schedule { [weak self] in self?.saveWhenAllowed() }
    }

    /// Section 4.7; no-op when clean; one at a time; a change during a save saves again after.
    func save() async {
        guard isDirty, !isReadOnly else { return }
        if isSaving {
            saveAgain = true
            return
        }
        isSaving = true
        saveDebouncer.cancel()
        pendingSave?.cancel()
        pendingSave = nil
        lastSaveBegan = Date()
        let end = beginBackgroundTask("save")
        defer { end() }

        let target = url
        guard let data = currentPDFData() else {
            lastError = .cannotSave(target, reason: "The note's pages could not be written.")
            isSaving = false
            return
        }
        let snapshot = NoteSnapshot(pdfData: data, drawings: pages.map(\.drawing),
                                    geometries: pages.indices.map { geometry(at: $0) }, pageCount: pages.count,
                                    noteID: noteID)
        let snapshotRevision = revision
        let known = self.known
        let root = libraryRoot
        let previous: URL? = wroteThisSession ? nil : SidecarLayout.previousVersion(forNote: target, libraryRoot: root)
        do {
            let result = try await Task.detached(priority: .userInitiated) { () throws -> WriteResult in
                if let previous { try? FileStore.createFolder(previous.deletingLastPathComponent()) }
                return try NoteWriter.write(snapshot, to: target, known: known, keepPrevious: previous)
            }.value
            wroteThisSession = true
            self.known = result.saved
            savedRevision = snapshotRevision
            isDirty = revision != savedRevision
            if let original = result.conflictWith {
                url = result.saved.url
                lastError = .conflict(original: original, copy: result.saved.url)
            } else if case .cannotSave? = lastError {
                lastError = nil
            }
            let template = self.template, lastPage = self.lastPage, noteID = self.noteID
            do {
                try MetadataStore.update(forNote: url, libraryRoot: root) { metadata in
                    metadata.template = template
                    metadata.lastPage = lastPage
                    metadata.noteID = noteID
                }
            } catch {
                log.error("meta.json not written: \(error.localizedDescription, privacy: .public)")
            }
            onSaved?()
        } catch {
            lastError = (error as? ZenithError) ?? .cannotSave(target, reason: error.localizedDescription)
            log.error("save failed: \(error.localizedDescription, privacy: .public)")
        }
        isSaving = false
        if saveAgain {
            saveAgain = false
            if isDirty { await save() }
        }
    }

    /// dropTrailingEmptyAutoPages, save, stop security-scoped access, clear undo.
    func close() async {
        dropTrailingEmptyAutoPages()
        saveDebouncer.cancel()
        pendingSave?.cancel()
        pendingSave = nil
        while isSaving {
            try? await Task.sleep(nanoseconds: 20_000_000)
        }
        await save()
        if let scopedURL {
            scopedURL.stopAccessingSecurityScopedResource()
            self.scopedURL = nil
        }
        undoManager.removeAllActions()
    }

    // Primitives for WP4 (no undo registration here):
    func insertPage(_ page: PDFPage, notePage: NotePage, at index: Int) {
        let index = min(max(index, 0), pages.count)
        pdf.insert(page, at: index)
        pages.insert(notePage, at: index)
    }

    func removePage(at index: Int) -> (PDFPage, NotePage) {
        guard pages.indices.contains(index), let page = pdf.page(at: index) else { return (PDFPage(), NotePage()) }
        pdf.removePage(at: index)
        return (page, pages.remove(at: index))
    }

    func movePage(from source: Int, to destination: Int) {
        guard pages.indices.contains(source), let page = pdf.page(at: source) else { return }
        let destination = min(max(destination, 0), pages.count - 1)
        guard destination != source else { return }
        pdf.removePage(at: source)
        pdf.insert(page, at: destination)
        let notePage = pages.remove(at: source)
        pages.insert(notePage, at: destination)
    }

    // MARK: Private

    private func noteChange() {
        isDirty = true
        revision += 1
        scheduleSave()
    }

    /// The idle delay has passed; the save waits further while the previous one began less than minimumGap ago.
    private func saveWhenAllowed() {
        let wait = lastSaveBegan.map { NoteDocument.minimumGap - Date().timeIntervalSince($0) } ?? 0
        pendingSave?.cancel()
        pendingSave = Task { [weak self] in
            if wait > 0 {
                try? await Task.sleep(nanoseconds: UInt64(wait * 1_000_000_000))
                guard !Task.isCancelled else { return }
            }
            await self?.save()
        }
    }
}

/// The identifier of one background task, shared by its expiration handler and the function that ends it.
private final class NoteSaveTask: @unchecked Sendable {
    var id: UIBackgroundTaskIdentifier = .invalid

    @MainActor func end(_ app: UIApplication) {
        guard id != .invalid else { return }
        app.endBackgroundTask(id)
        id = .invalid
    }
}
