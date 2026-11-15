import Foundation

/// meta.json read-modify-write, orphaned sidecars.
enum MetadataStore {
    /// Defaults when missing or bad.
    static func load(forNote note: URL, libraryRoot: URL?) -> NoteMetadata {
        NoteMetadata()
    }

    /// Reads meta.json, applies `change`, writes it back, all inside one coordinated write of that file, so writers of
    /// different fields (the open note, the library) never undo each other's changes.
    static func update(forNote note: URL, libraryRoot: URL?, _ change: (inout NoteMetadata) -> Void) throws {
    }

    /// For a note whose sidecar folder is missing: looks in the note's own .zenith folder, then in the library's other
    /// .zenith folders, for a meta.json whose noteID matches and whose note file no longer exists, and moves that
    /// sidecar to the note's name. Returns the sidecar folder in use, or nil when there was nothing to adopt.
    @discardableResult static func adoptOrphanedSidecar(forNote note: URL, noteID: String, libraryRoot: URL?) throws -> URL? {
        nil
    }
}
