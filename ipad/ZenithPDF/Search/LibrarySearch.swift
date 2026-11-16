import Foundation
import PDFKit

/// Equal and hashed by id only (the note's URL).
struct LibrarySearchResult: Identifiable, Hashable, Sendable {
    let note: NoteItem
    let titleMatch: Bool
    let hits: [SearchHit]
    var id: URL { note.url }

    static func == (a: LibrarySearchResult, b: LibrarySearchResult) -> Bool {
        a.id == b.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
}

/// Search across notes.
enum LibrarySearch {
    /// Notes whose title or sidecar index matches, in the order given; [] for an empty query or a cancelled task.
    static func search(_ query: String, in notes: [NoteItem], libraryRoot: URL) async -> [LibrarySearchResult] {
        guard !query.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { return [] }
        var results: [LibrarySearchResult] = []
        for note in notes {
            if Task.isCancelled { return [] }
            // The title goes through the same matching as page text.
            let title = SearchIndex(pages: [PageText(typed: note.title, words: [], inkHash: "")])
            let titleMatch = !title.hits(for: query).isEmpty
            let index = SearchIndex.load(note: note.url, libraryRoot: libraryRoot) ?? typedIndex(of: note.url)
            let hits = index?.hits(for: query) ?? []
            if titleMatch || !hits.isEmpty {
                results.append(LibrarySearchResult(note: note, titleMatch: titleMatch, hits: hits))
            }
        }
        return results
    }

    /// shortcut: a note never indexed on the iPad (written on the PC) is searched by its typed text only, read
    /// from the PDF on every search; index such notes in the background if large libraries make this slow.
    private static func typedIndex(of url: URL) -> SearchIndex? {
        guard let pdf = PDFDocument(url: url) else { return nil }
        return SearchIndex(pages: (0..<pdf.pageCount).map {
            PageText(typed: pdf.page(at: $0)?.string ?? "", words: [], inkHash: "")
        })
    }
}
