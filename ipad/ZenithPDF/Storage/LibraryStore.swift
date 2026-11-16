import Foundation

/// Listing and file operations of the library.
final class LibraryStore: @unchecked Sendable {   // no mutable state: safe from any thread
    let root: URL

    init(root: URL) {
        self.root = root
    }

    /// nil = root; hidden, "Inbox" at root skipped.
    func folders(in folder: URL?) throws -> [FolderItem] {
        let parent = folder ?? root
        return try visibleEntries(of: parent)
            .filter { isDirectory($0) }
            .map { url in
                FolderItem(url: canonical(url), name: url.lastPathComponent,
                           noteCount: ((try? notes(in: url, recursive: false)) ?? []).count)
            }
            .sorted { $0.name.localizedStandardCompare($1.name) == .orderedAscending }
    }

    /// *.pdf only, hidden skipped.
    func notes(in folder: URL?, recursive: Bool) throws -> [NoteItem] {
        let parent = folder ?? root
        var result: [NoteItem] = []
        var pending = [parent]
        while let current = pending.popLast() {
            let entries = try FileManager.default.contentsOfDirectory(at: current, includingPropertiesForKeys: [.isDirectoryKey])
            let folderPath = relativePath(of: current)
            for entry in entries {
                let name = entry.lastPathComponent
                if let cloudName = LibraryStore.placeholderName(name) {
                    let note = current.appendingPathComponent(cloudName, isDirectory: false)
                    if !FileStore.exists(note) { result.append(item(for: note, dateSource: entry, folderPath: folderPath)) }
                    continue
                }
                if SidecarLayout.isHidden(entry) { continue }
                if isDirectory(entry) {
                    if recursive && !isInbox(entry) { pending.append(entry) }
                } else if entry.pathExtension.lowercased() == "pdf" {
                    result.append(item(for: entry, dateSource: entry, folderPath: folderPath))
                }
            }
        }
        return result
    }

    func favourites() throws -> [NoteItem] {
        try notes(in: nil, recursive: true).filter(\.isFavourite)
    }

    func createFolder(named name: String, in parent: URL?) throws -> URL {
        let valid = try FileStore.validatedName(name)
        let url = FileStore.uniqueURL((parent ?? root).appendingPathComponent(valid, isDirectory: true))
        try FileStore.createFolder(url)
        return canonical(url)
    }

    /// Callers render a template with TemplateRenderer.pdfData; the template is recorded in meta.json when given.
    func createNote(named name: String, pdfData: Data, template: PaperTemplate?, in folder: URL?) throws -> URL {
        let valid = try FileStore.validatedName(LibraryStore.withoutPDFExtension(FileStore.validatedName(name)))
        let url = FileStore.uniqueURL((folder ?? root).appendingPathComponent(valid + ".pdf", isDirectory: false))
        try FileStore.replace(url, with: pdfData)
        try MetadataStore.update(forNote: url, libraryRoot: root) { metadata in
            metadata.created = Date()
            if let template { metadata.template = template }
        }
        return canonical(url)
    }

    /// Copies; security scope handled by caller.
    func importPDF(from source: URL, into folder: URL?) throws -> URL {
        let title = LibraryStore.withoutPDFExtension(source.lastPathComponent)
        let name = (try? FileStore.validatedName(title)) ?? tr("Untitled")
        let url = FileStore.uniqueURL((folder ?? root).appendingPathComponent(name + ".pdf", isDirectory: false))
        try FileStore.copy(source, to: url)
        return canonical(url)
    }

    /// Note or folder; a note's sidecar follows.
    func rename(_ url: URL, to newName: String) throws -> URL {
        let folder = isDirectory(url)
        var name = try FileStore.validatedName(newName)
        if !folder {
            name = LibraryStore.withoutPDFExtension(name)
            _ = try FileStore.validatedName(name)
            name += ".pdf"
        }
        let destination = url.deletingLastPathComponent().appendingPathComponent(name, isDirectory: folder)
        if destination.lastPathComponent == url.lastPathComponent { return canonical(url) }
        // A change of case only is the same file on a case-insensitive volume, not a name taken by another item.
        let caseOnly = destination.lastPathComponent.lowercased() == url.lastPathComponent.lowercased()
        let siblings = (try? FileManager.default.contentsOfDirectory(atPath: url.deletingLastPathComponent().path)) ?? []
        if siblings.contains(destination.lastPathComponent) || (!caseOnly && FileStore.exists(destination)) {
            throw ZenithError.nameTaken(name)
        }
        if caseOnly {
            let step = url.deletingLastPathComponent()
                .appendingPathComponent(UUID().uuidString + (folder ? "" : ".pdf"), isDirectory: folder)
            try moveItem(url, to: step, isFolder: folder)
            try moveItem(step, to: destination, isFolder: folder)
        } else {
            try moveItem(url, to: destination, isFolder: folder)
        }
        return canonical(destination)
    }

    /// Note (with sidecar, new noteID in its meta.json) or folder.
    func duplicate(_ url: URL) throws -> URL {
        let folder = isDirectory(url)
        let destination = FileStore.uniqueURL(url.deletingLastPathComponent()
            .appendingPathComponent(url.lastPathComponent, isDirectory: folder))
        try FileStore.copy(url, to: destination)
        var copies = [destination]
        if folder {
            copies = try notes(in: destination, recursive: true).map(\.url)
        } else {
            let sidecar = SidecarLayout.folder(forNote: url, libraryRoot: root)
            if FileStore.exists(sidecar) {
                try FileStore.copy(sidecar, to: SidecarLayout.folder(forNote: destination, libraryRoot: root))
            }
        }
        for copy in copies where FileStore.exists(SidecarLayout.metadata(forNote: copy, libraryRoot: root)) {
            try MetadataStore.update(forNote: copy, libraryRoot: root) { metadata in
                metadata.noteID = UUID().uuidString
                metadata.created = Date()
            }
        }
        return canonical(destination)
    }

    /// Sidecar follows; name made unique.
    func move(_ url: URL, to folder: URL?) throws -> URL {
        let target = folder ?? root
        let isFolder = isDirectory(url)
        if components(of: url.deletingLastPathComponent()) == components(of: target) { return canonical(url) }
        if isFolder && SidecarLayout.isInside(target, root: url) {
            throw ZenithError.cannotSave(url, reason: "A folder cannot move into itself.")
        }
        let destination = FileStore.uniqueURL(target.appendingPathComponent(url.lastPathComponent, isDirectory: isFolder))
        try moveItem(url, to: destination, isFolder: isFolder)
        return canonical(destination)
    }

    /// MetadataStore.update of isFavourite only.
    func setFavourite(_ on: Bool, for note: URL) throws {
        try MetadataStore.update(forNote: note, libraryRoot: root) { $0.isFavourite = on }
    }

    func relativePath(of url: URL) -> String {
        let path = components(of: url), base = components(of: root)
        guard path.count >= base.count, Array(path.prefix(base.count)) == base else { return url.lastPathComponent }
        return path.dropFirst(base.count).joined(separator: "/")
    }

    func url(forRelativePath path: String) -> URL {
        path.isEmpty ? root : root.appendingPathComponent(path)
    }

    /// name: localizedStandardCompare
    static func sorted(_ notes: [NoteItem], by sort: LibrarySort) -> [NoteItem] {
        notes.sorted { a, b in
            let byName = a.title.localizedStandardCompare(b.title)
            switch sort {
            case .modified where a.modified != b.modified: return a.modified > b.modified
            case .created where a.created != b.created: return a.created > b.created
            default:
                return byName == .orderedSame ? a.folderPath < b.folderPath : byName == .orderedAscending
            }
        }
    }

    // MARK: - Private

    /// Moves a note with its sidecar folder, or a folder (whose sidecars are inside it).
    private func moveItem(_ url: URL, to destination: URL, isFolder: Bool) throws {
        try FileStore.move(url, to: destination)
        guard !isFolder else { return }
        let sidecar = SidecarLayout.folder(forNote: url, libraryRoot: root)
        guard FileStore.exists(sidecar) else { return }
        let newSidecar = SidecarLayout.folder(forNote: destination, libraryRoot: root)
        // A sidecar already at the new name belongs to no note there (the name was free): it would mix with ours.
        if FileStore.exists(newSidecar) { try FileStore.remove(newSidecar) }
        try FileStore.move(sidecar, to: newSidecar)
    }

    private func item(for note: URL, dateSource: URL, folderPath: String) -> NoteItem {
        let metadata = MetadataStore.load(forNote: note, libraryRoot: root)
        let modified = FileStore.modificationDate(dateSource) ?? Date.distantPast
        return NoteItem(url: canonical(note), title: LibraryStore.withoutPDFExtension(note.lastPathComponent),
                        modified: modified,
                        created: metadata.created ?? FileStore.creationDate(dateSource) ?? modified,
                        isFavourite: metadata.isFavourite, folderPath: folderPath)
    }

    /// Entries of a folder a person sees: no hidden names, no Inbox at the root.
    private func visibleEntries(of folder: URL) throws -> [URL] {
        try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: [.isDirectoryKey])
            .filter { !SidecarLayout.isHidden($0) && !isInbox($0) }
    }

    private func isInbox(_ url: URL) -> Bool {
        url.lastPathComponent == "Inbox" && components(of: url.deletingLastPathComponent()) == components(of: root)
    }

    private func isDirectory(_ url: URL) -> Bool {
        var directory: ObjCBool = false
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &directory) && directory.boolValue
    }

    private func canonical(_ url: URL) -> URL {
        url.standardizedFileURL.resolvingSymlinksInPath()
    }

    /// Path components with "/private" dropped, so "/private/var/..." and "/var/..." compare equal (pitfall P12).
    private func components(of url: URL) -> [String] {
        var path = canonical(url).path
        if path.hasPrefix("/private/") { path = String(path.dropFirst("/private".count)) }
        return path.split(separator: "/").map(String.init)
    }

    /// "<name>.pdf" for an iCloud placeholder ".<name>.pdf.icloud", else nil.
    private static func placeholderName(_ name: String) -> String? {
        guard name.hasPrefix("."), name.hasSuffix(".icloud") else { return nil }
        let inner = String(name.dropFirst().dropLast(".icloud".count))
        guard !inner.hasPrefix("."), inner.lowercased().hasSuffix(".pdf"), inner.count > 4 else { return nil }
        return inner
    }

    private static func withoutPDFExtension(_ name: String) -> String {
        name.lowercased().hasSuffix(".pdf") ? String(name.dropLast(4)) : name
    }
}
