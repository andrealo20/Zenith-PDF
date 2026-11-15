import Foundation

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
    static func search(_ query: String, in notes: [NoteItem], libraryRoot: URL) async -> [LibrarySearchResult] {
        []
    }
}
