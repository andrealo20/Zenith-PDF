import CryptoKit
import Foundation
import os
import PDFKit
import PencilKit

/// Keeps sidecar indexes current.
@MainActor final class SearchIndexer {
    static let shared = SearchIndexer()

    private var debouncers: [URL: Debouncer] = [:]
    private var running: [URL: Task<Void, Never>] = [:]
    // Typed text of the last note indexed, kept while its revision stays the same, so repeated searches do not
    // extract the text of every page again.
    private weak var typedDocument: NoteDocument?
    private var typedRevision = 0
    private var typedPages: [String] = []

    /// 5 s after the last call for that note.
    func schedule(_ document: NoteDocument) {
        let key = document.url
        let debouncer = debouncers[key] ?? Debouncer(seconds: 5)
        debouncers[key] = debouncer
        debouncer.schedule { [weak self] in
            self?.debouncers[key] = nil
            Task { await self?.indexNow(document) }
        }
    }

    /// Pages whose inkHash changed; typed text for all.
    /// Calls for one note run one after the other, so no page is recognised twice at the same time.
    func indexNow(_ document: NoteDocument) async {
        let key = document.url
        let previous = running[key]
        let task = Task {
            await previous?.value
            await self.refresh(document)
        }
        running[key] = task
        await task.value
        if running[key] == task { running[key] = nil }
    }

    private func refresh(_ document: NoteDocument) async {
        let root = document.libraryRoot
        let old = SearchIndex.load(note: document.url, libraryRoot: root)
        var known: [String: [InkWord]] = [:]
        for page in old?.pages ?? [] { known[page.inkHash] = page.words }

        // Taken before the first suspension, so the index describes one state of the note.
        let count = document.pdf.pageCount
        let drawings = (0..<count).map { document.pages.indices.contains($0) ? document.pages[$0].drawing : PKDrawing() }
        let sizes = (0..<count).map { document.geometry(at: $0).displaySize }
        let typed = await typedText(of: document)

        var pages: [PageText] = []
        for index in 0..<count {
            let drawing = drawings[index]
            let hash = SearchIndex.inkHash(of: drawing)
            var words = known[hash] ?? []
            if known[hash] == nil, !drawing.strokes.isEmpty,
               let image = HandwritingRecognizer.image(of: drawing, pageSize: sizes[index]) {
                // A failure is stored like an empty result, so the page is tried again only when its ink changes.
                words = (try? await HandwritingRecognizer.words(in: image, pageSize: sizes[index])) ?? []
            }
            known[hash] = words
            pages.append(PageText(typed: index < typed.count ? typed[index] : "", words: words, inkHash: hash))
        }

        guard old?.pages != pages else { return }
        do {
            try SearchIndex(pages: pages).save(note: document.url, libraryRoot: root)
        } catch {
            log.error("Search index not saved: \(error.localizedDescription, privacy: .public)")
        }
    }

    /// PDFPage.string of every page, read from a copy of the bytes off the main actor.
    private func typedText(of document: NoteDocument) async -> [String] {
        if typedDocument === document, typedRevision == document.revision { return typedPages }
        let revision = document.revision
        var typed: [String]? = nil
        if let data = document.currentPDFData() {
            typed = await Task.detached(priority: .utility) { () -> [String]? in
                guard let pdf = PDFDocument(data: data) else { return nil }
                return (0..<pdf.pageCount).map { pdf.page(at: $0)?.string ?? "" }
            }.value
        }
        let pages = typed ?? (0..<document.pdf.pageCount).map { document.pdf.page(at: $0)?.string ?? "" }
        typedDocument = document
        typedRevision = revision
        typedPages = pages
        return pages
    }
}
