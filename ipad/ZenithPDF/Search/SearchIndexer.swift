import Foundation

/// Keeps sidecar indexes current.
@MainActor final class SearchIndexer {
    static let shared = SearchIndexer()

    /// 5 s after the last call for that note.
    func schedule(_ document: NoteDocument) {
    }

    /// Pages whose inkHash changed; typed text for all.
    func indexNow(_ document: NoteDocument) async {
    }
}
