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
        var directory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &directory) else {
            throw ZenithError.cannotOpen(url)
        }
        let isFolder = directory.boolValue
        let item = TrashedItem(id: UUID(),
                               title: isFolder ? url.lastPathComponent : url.deletingPathExtension().lastPathComponent,
                               originalPath: library.relativePath(of: url),
                               // Whole seconds, as item.json keeps them, so the item returned equals the one listed.
                               deletedAt: Date(timeIntervalSince1970: now().timeIntervalSince1970.rounded(.down)),
                               isFolder: isFolder)
        let slot = self.slot(for: item)
        // item.json first: a slot whose move failed is removed, a moved item is never left without its record.
        try FileStore.replace(slot.appendingPathComponent(TrashStore.recordName), with: encoder().encode(item))
        do {
            _ = try library.move(url, to: slot)
        } catch {
            try? FileStore.remove(slot)
            throw error
        }
        return item
    }

    /// Newest first.
    func items() throws -> [TrashedItem] {
        guard FileStore.exists(folder) else { return [] }
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .iso8601
        return try FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)
            .compactMap { slot in
                guard let data = try? Data(contentsOf: slot.appendingPathComponent(TrashStore.recordName)) else { return nil }
                return try? decoder.decode(TrashedItem.self, from: data)
            }
            .sorted { $0.deletedAt > $1.deletedAt }
    }

    /// Original place, unique name, folders recreated.
    func restore(_ item: TrashedItem) throws -> URL {
        let slot = self.slot(for: item)
        let entries = try FileManager.default.contentsOfDirectory(at: slot, includingPropertiesForKeys: nil)
        guard let content = entries.first(where: { !SidecarLayout.isHidden($0) && $0.lastPathComponent != TrashStore.recordName })
        else { throw ZenithError.cannotOpen(slot) }
        let original = library.url(forRelativePath: item.originalPath)
        let parent = item.originalPath.contains("/") ? original.deletingLastPathComponent() : library.root
        try FileStore.createFolder(parent)
        let restored = try library.move(content, to: parent)
        try? FileStore.remove(slot)
        return restored
    }

    func deletePermanently(_ item: TrashedItem) throws {
        let slot = self.slot(for: item)
        if FileStore.exists(slot) { try FileStore.remove(slot) }
    }

    func empty() throws {
        if FileStore.exists(folder) { try FileStore.remove(folder) }
    }

    @discardableResult func purge(olderThanDays days: Int = 30) throws -> Int {
        let cutoff = now().addingTimeInterval(-Double(days) * 86_400)
        let old = try items().filter { $0.deletedAt < cutoff }
        for item in old { try deletePermanently(item) }
        return old.count
    }

    // MARK: - Private

    private static let recordName = "item.json"

    private var folder: URL {
        library.root.appendingPathComponent(SidecarLayout.trashName, isDirectory: true)
    }

    private func slot(for item: TrashedItem) -> URL {
        folder.appendingPathComponent(item.id.uuidString, isDirectory: true)
    }

    private func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }
}
