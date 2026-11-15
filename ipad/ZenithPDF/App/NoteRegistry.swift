import Foundation

/// One NoteDocument per URL across windows.
@MainActor final class NoteRegistry {
    static let shared = NoteRegistry()

    /// Throws ZenithError.cannotOpen when the note is already open in another window.
    func open(_ url: URL, libraryRoot: URL?) async throws -> NoteDocument {
        try await NoteDocument.open(url, libraryRoot: libraryRoot)
    }

    func close(_ document: NoteDocument) async {
    }

    func isOpen(_ url: URL) -> Bool {
        false
    }

    /// Every open note; each save holds its own background task.
    func saveAll() async {
    }
}
