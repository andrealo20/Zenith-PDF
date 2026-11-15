import Foundation

/// One NoteDocument per URL across windows.
@MainActor final class NoteRegistry {
    static let shared = NoteRegistry()

    private var documents: [String: NoteDocument] = [:]
    private var opening: Set<String> = []
    /// Closes still saving, by key: an open of the same file waits for them, so it reads what the close wrote.
    private var closing: [String: Task<Void, Never>] = [:]

    /// Throws ZenithError.cannotOpen when the note is already open in another window.
    func open(_ url: URL, libraryRoot: URL?) async throws -> NoteDocument {
        let key = NoteRegistry.key(for: url)
        if let pending = closing[key] { await pending.value }
        guard documents[key] == nil, !opening.contains(key) else { throw ZenithError.cannotOpen(url) }
        opening.insert(key)
        defer { opening.remove(key) }
        let document = try await NoteDocument.open(url, libraryRoot: libraryRoot)
        documents[key] = document
        return document
    }

    func close(_ document: NoteDocument) async {
        guard let key = documents.first(where: { $0.value === document })?.key else {
            // A second close of the same note waits for the first one.
            if let pending = closing[NoteRegistry.key(for: document.url)] { await pending.value }
            return
        }
        documents[key] = nil
        let task = Task { await document.close() }
        closing[key] = task
        await task.value
        if closing[key] == task { closing[key] = nil }
    }

    func isOpen(_ url: URL) -> Bool {
        let key = NoteRegistry.key(for: url)
        return documents[key] != nil || opening.contains(key)
    }

    /// Every open note; each save holds its own background task.
    func saveAll() async {
        for document in Array(documents.values) {
            await document.save()
        }
    }

    /// One spelling per file: standardized, symlinks resolved, "/private/var" read as "/var" (pitfall P12).
    private static func key(for url: URL) -> String {
        var path = url.standardizedFileURL.resolvingSymlinksInPath().path
        if path.hasPrefix("/private/") { path = String(path.dropFirst("/private".count)) }
        return path
    }
}
