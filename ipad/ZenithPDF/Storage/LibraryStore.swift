import Foundation

/// Listing and file operations of the library.
final class LibraryStore: @unchecked Sendable {   // no mutable state: safe from any thread
    let root: URL

    init(root: URL) {
        self.root = root
    }

    /// nil = root; hidden, "Inbox" at root skipped.
    func folders(in folder: URL?) throws -> [FolderItem] {
        []
    }

    /// *.pdf only, hidden skipped.
    func notes(in folder: URL?, recursive: Bool) throws -> [NoteItem] {
        []
    }

    func favourites() throws -> [NoteItem] {
        []
    }

    func createFolder(named name: String, in parent: URL?) throws -> URL {
        (parent ?? root).appendingPathComponent(name, isDirectory: true)
    }

    /// Callers render a template with TemplateRenderer.pdfData; the template is recorded in meta.json when given.
    func createNote(named name: String, pdfData: Data, template: PaperTemplate?, in folder: URL?) throws -> URL {
        (folder ?? root).appendingPathComponent(name + ".pdf", isDirectory: false)
    }

    /// Copies; security scope handled by caller.
    func importPDF(from source: URL, into folder: URL?) throws -> URL {
        (folder ?? root).appendingPathComponent(source.lastPathComponent, isDirectory: false)
    }

    /// Note or folder; a note's sidecar follows.
    func rename(_ url: URL, to newName: String) throws -> URL {
        url
    }

    /// Note (with sidecar, new noteID in its meta.json) or folder.
    func duplicate(_ url: URL) throws -> URL {
        url
    }

    /// Sidecar follows; name made unique.
    func move(_ url: URL, to folder: URL?) throws -> URL {
        url
    }

    /// MetadataStore.update of isFavourite only.
    func setFavourite(_ on: Bool, for note: URL) throws {
    }

    func relativePath(of url: URL) -> String {
        ""
    }

    func url(forRelativePath path: String) -> URL {
        root.appendingPathComponent(path)
    }

    /// name: localizedStandardCompare
    static func sorted(_ notes: [NoteItem], by sort: LibrarySort) -> [NoteItem] {
        notes
    }
}
