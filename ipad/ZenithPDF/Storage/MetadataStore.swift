import Foundation

/// meta.json read-modify-write, orphaned sidecars.
enum MetadataStore {
    /// Defaults when missing or bad.
    static func load(forNote note: URL, libraryRoot: URL?) -> NoteMetadata {
        // A plain read: listings load every note's metadata and must not wait on a coordinated cloud download.
        let url = SidecarLayout.metadata(forNote: note, libraryRoot: libraryRoot)
        return decode(try? Data(contentsOf: url)) ?? NoteMetadata()
    }

    /// Reads meta.json, applies `change`, writes it back, all inside one coordinated write of that file, so writers of
    /// different fields (the open note, the library) never undo each other's changes.
    static func update(forNote note: URL, libraryRoot: URL?, _ change: (inout NoteMetadata) -> Void) throws {
        let url = SidecarLayout.metadata(forNote: note, libraryRoot: libraryRoot)
        try FileStore.createFolder(url.deletingLastPathComponent())
        var coordinationError: NSError?
        var failure: Error?
        NSFileCoordinator(filePresenter: nil).coordinate(writingItemAt: url, options: .forMerging,
                                                         error: &coordinationError) { newURL in
            do {
                var metadata = decode(try? Data(contentsOf: newURL)) ?? NoteMetadata()
                change(&metadata)
                // Written here and not through FileStore.replace: a second coordinator on the same file would wait
                // for this one and never get its turn.
                try encoder().encode(metadata).write(to: newURL, options: .atomic)
            } catch {
                failure = error
            }
        }
        if let coordinationError { throw coordinationError }
        if let failure { throw failure }
    }

    /// For a note whose sidecar folder is missing: looks in the note's own .zenith folder, then in the library's other
    /// .zenith folders, for a meta.json whose noteID matches and whose note file no longer exists, and moves that
    /// sidecar to the note's name. Returns the sidecar folder in use, or nil when there was nothing to adopt.
    @discardableResult static func adoptOrphanedSidecar(forNote note: URL, noteID: String, libraryRoot: URL?) throws -> URL? {
        let target = SidecarLayout.folder(forNote: note, libraryRoot: libraryRoot)
        if FileStore.exists(target) { return target }
        guard let root = libraryRoot, !noteID.isEmpty else { return nil }
        let ownFolder = note.deletingLastPathComponent()
        let folders = [ownFolder] + noteFolders(under: root).filter { $0.standardizedFileURL.path != ownFolder.standardizedFileURL.path }
        for folder in folders {
            let sidecars = folder.appendingPathComponent(SidecarLayout.folderName, isDirectory: true)
            let entries = (try? FileManager.default.contentsOfDirectory(at: sidecars, includingPropertiesForKeys: nil)) ?? []
            for sidecar in entries.sorted(by: { $0.lastPathComponent < $1.lastPathComponent }) {
                let owner = folder.appendingPathComponent(sidecar.lastPathComponent, isDirectory: false)
                guard !FileStore.exists(owner),
                      !FileStore.exists(folder.appendingPathComponent("." + owner.lastPathComponent + ".icloud")),
                      let metadata = decode(try? Data(contentsOf: sidecar.appendingPathComponent("meta.json"))),
                      metadata.noteID == noteID else { continue }
                try FileStore.move(sidecar, to: target)
                return target
            }
        }
        return nil
    }

    // MARK: - Private

    private static func decode(_ data: Data?) -> NoteMetadata? {
        guard let data else { return nil }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try? decoder.decode(NoteMetadata.self, from: data)
    }

    private static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }

    /// The root and every folder below it that is not hidden (so no sidecar or trash folder).
    private static func noteFolders(under root: URL) -> [URL] {
        var result = [root]
        var index = 0
        while index < result.count {
            let entries = (try? FileManager.default.contentsOfDirectory(at: result[index],
                                                                         includingPropertiesForKeys: [.isDirectoryKey])) ?? []
            for entry in entries where !SidecarLayout.isHidden(entry) {
                if (try? entry.resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory == true { result.append(entry) }
            }
            index += 1
        }
        return result
    }
}
