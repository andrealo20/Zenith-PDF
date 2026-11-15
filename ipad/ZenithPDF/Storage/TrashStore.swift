import Foundation

/// Recently Deleted.
final class TrashStore: @unchecked Sendable {
    let library: LibraryStore
    private let now: @Sendable () -> Date

    init(library: LibraryStore, now: @escaping @Sendable () -> Date = { Date() }) {
        self.library = library
        self.now = now
    }

    /// Moves the note (with its sidecar) or folder to <root>/.zenith-trash/<uuid>/ and writes item.json there.
    func trash(_ url: URL) throws -> TrashedItem {
        TrashedItem(id: UUID(), title: url.deletingPathExtension().lastPathComponent,
                    originalPath: library.relativePath(of: url), deletedAt: now(), isFolder: false)
    }

    /// Newest first.
    func items() throws -> [TrashedItem] {
        []
    }

    /// Original place, unique name, folders recreated.
    func restore(_ item: TrashedItem) throws -> URL {
        library.url(forRelativePath: item.originalPath)
    }

    func deletePermanently(_ item: TrashedItem) throws {
    }

    func empty() throws {
    }

    @discardableResult func purge(olderThanDays days: Int = 30) throws -> Int {
        0
    }
}
