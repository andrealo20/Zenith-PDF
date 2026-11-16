import CryptoKit
import Foundation

/// Coordinated, safe file operations.
enum FileStore {
    /// NSFileCoordinator read; triggers download of cloud files.
    static func read(_ url: URL) throws -> Data {
        if !exists(url), exists(placeholder(for: url)) {
            try? FileManager.default.startDownloadingUbiquitousItem(at: url)
        }
        var coordinationError: NSError?
        var result: Result<Data, Error> = .failure(ZenithError.cannotOpen(url))
        NSFileCoordinator(filePresenter: nil).coordinate(readingItemAt: url, options: [], error: &coordinationError) { newURL in
            result = Result { try Data(contentsOf: newURL) }
        }
        if let coordinationError { throw coordinationError }
        return try result.get()
    }

    /// Coordinated replace (options .forReplacing). Inside the accessor everything uses the URL the coordinator hands
    /// over (newURL), never the one passed in: data -> temporary file in the item replacement directory for newURL ->
    /// FileManager.replaceItemAt(newURL, withItemAt:). When the replacement directory cannot be made or replaceItemAt
    /// throws (some file providers), data.write(to: newURL, options: .atomic) inside the same coordination.
    /// When `keepPrevious` is given and the file exists, it is copied there first. Creates the file when it does not
    /// exist. Returns the new modification date.
    @discardableResult static func replace(_ url: URL, with data: Data, keepPrevious: URL? = nil) throws -> Date {
        try createFolder(url.deletingLastPathComponent())
        let manager = FileManager.default
        var coordinationError: NSError?
        var failure: Error?
        var written = Date()
        NSFileCoordinator(filePresenter: nil).coordinate(writingItemAt: url, options: .forReplacing,
                                                         error: &coordinationError) { newURL in
            do {
                let existed = manager.fileExists(atPath: newURL.path)
                if existed, let keepPrevious {
                    try createFolder(keepPrevious.deletingLastPathComponent())
                    if manager.fileExists(atPath: keepPrevious.path) { try manager.removeItem(at: keepPrevious) }
                    try manager.copyItem(at: newURL, to: keepPrevious)
                }
                if !existed || !swapIn(data, at: newURL) {
                    try data.write(to: newURL, options: .atomic)
                }
                written = modificationDate(newURL) ?? Date()
            } catch {
                failure = error
            }
        }
        if let coordinationError { throw coordinationError }
        if let failure { throw failure }
        return written
    }

    /// Test hook: how replace finds its temporary folder. Default FileManager.url(for: .itemReplacementDirectory, ...).
    nonisolated(unsafe) static var replacementDirectory: (URL) throws -> URL = { url in
        try FileManager.default.url(for: .itemReplacementDirectory, in: .userDomainMask, appropriateFor: url, create: true)
    }

    /// NSFileVersion.unresolvedConflictVersionsOfItem(at:) not empty.
    static func hasUnresolvedConflicts(_ url: URL) -> Bool {
        !(NSFileVersion.unresolvedConflictVersionsOfItem(at: url) ?? []).isEmpty
    }

    /// Lowercase hex (CryptoKit).
    static func sha256(_ data: Data) -> String {
        SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    /// Coordinated, fails if destination exists. Creates the destination's folder when missing.
    static func move(_ source: URL, to destination: URL) throws {
        if exists(destination) { throw ZenithError.nameTaken(destination.lastPathComponent) }
        try createFolder(destination.deletingLastPathComponent())
        let coordinator = NSFileCoordinator(filePresenter: nil)
        var coordinationError: NSError?
        var failure: Error?
        coordinator.coordinate(writingItemAt: source, options: .forMoving, writingItemAt: destination,
                               options: .forReplacing, error: &coordinationError) { from, to in
            do {
                try FileManager.default.moveItem(at: from, to: to)
                coordinator.item(at: from, didMoveTo: to)
            } catch {
                failure = error
            }
        }
        if let coordinationError { throw coordinationError }
        if let failure { throw failure }
    }

    /// Coordinated, fails if destination exists. Creates the destination's folder when missing.
    static func copy(_ source: URL, to destination: URL) throws {
        if exists(destination) { throw ZenithError.nameTaken(destination.lastPathComponent) }
        try createFolder(destination.deletingLastPathComponent())
        var coordinationError: NSError?
        var failure: Error?
        NSFileCoordinator(filePresenter: nil).coordinate(readingItemAt: source, options: [], writingItemAt: destination,
                                                         options: .forReplacing, error: &coordinationError) { from, to in
            do {
                try FileManager.default.copyItem(at: from, to: to)
            } catch {
                failure = error
            }
        }
        if let coordinationError { throw coordinationError }
        if let failure { throw failure }
    }

    static func remove(_ url: URL) throws {
        var coordinationError: NSError?
        var failure: Error?
        NSFileCoordinator(filePresenter: nil).coordinate(writingItemAt: url, options: .forDeleting,
                                                         error: &coordinationError) { newURL in
            do {
                try FileManager.default.removeItem(at: newURL)
            } catch {
                failure = error
            }
        }
        if let coordinationError { throw coordinationError }
        if let failure { throw failure }
    }

    /// With intermediate folders, no error if present.
    static func createFolder(_ url: URL) throws {
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
    }

    static func modificationDate(_ url: URL) -> Date? {
        // Attributes, not URL resource values: those are cached on the URL object and go stale after a write.
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.modificationDate] as? Date
    }

    static func creationDate(_ url: URL) -> Date? {
        (try? FileManager.default.attributesOfItem(atPath: url.path))?[.creationDate] as? Date
    }

    static func exists(_ url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.path)
    }

    /// "Name.pdf", then "Name 2.pdf", "Name 3.pdf", ... (folders: "Name", "Name 2")
    /// A name also counts as taken while its iCloud placeholder or, for a note, a sidecar folder of that name exists,
    /// so a new note never inherits recordings or an index left behind by another one.
    static func uniqueURL(_ desired: URL) -> URL {
        guard isTaken(desired) else { return desired }
        let folder = desired.deletingLastPathComponent()
        let isFolder = desired.hasDirectoryPath || isDirectory(desired)
        let ext = isFolder ? "" : desired.pathExtension
        let base = ext.isEmpty ? desired.lastPathComponent : desired.deletingPathExtension().lastPathComponent
        var number = 2
        while true {
            let name = ext.isEmpty ? "\(base) \(number)" : "\(base) \(number).\(ext)"
            let candidate = folder.appendingPathComponent(name, isDirectory: isFolder)
            if !isTaken(candidate) { return candidate }
            number += 1
        }
    }

    /// Trims; rejects "", "/", ":", leading ".".
    static func validatedName(_ name: String) throws -> String {
        let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
        if trimmed.isEmpty || trimmed.hasPrefix(".") || trimmed.contains("/") || trimmed.contains(":") {
            throw ZenithError.invalidName(name)
        }
        return trimmed
    }

    // MARK: - Private

    /// Writes into the item replacement directory and swaps the file in; false when the provider refuses either step.
    private static func swapIn(_ data: Data, at url: URL) -> Bool {
        guard let folder = try? replacementDirectory(url) else { return false }
        defer { try? FileManager.default.removeItem(at: folder) }
        let temporary = folder.appendingPathComponent(url.lastPathComponent, isDirectory: false)
        do {
            try data.write(to: temporary, options: .atomic)
            _ = try FileManager.default.replaceItemAt(url, withItemAt: temporary)
            return true
        } catch {
            log.error("Replacing \(url.lastPathComponent, privacy: .public) in place failed: \(error.localizedDescription, privacy: .public)")
            return false
        }
    }

    private static func isTaken(_ url: URL) -> Bool {
        if exists(url) || exists(placeholder(for: url)) { return true }
        guard url.pathExtension.lowercased() == "pdf" else { return false }
        let sidecar = url.deletingLastPathComponent()
            .appendingPathComponent(SidecarLayout.folderName, isDirectory: true)
            .appendingPathComponent(url.lastPathComponent, isDirectory: true)
        return exists(sidecar)
    }

    /// iCloud Drive's stand-in for a file not downloaded yet: ".<name>.icloud" in the same folder.
    private static func placeholder(for url: URL) -> URL {
        url.deletingLastPathComponent().appendingPathComponent("." + url.lastPathComponent + ".icloud", isDirectory: false)
    }

    private static func isDirectory(_ url: URL) -> Bool {
        var directory: ObjCBool = false
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &directory) && directory.boolValue
    }
}
