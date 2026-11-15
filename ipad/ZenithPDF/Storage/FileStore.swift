import CryptoKit
import Foundation

/// Coordinated, safe file operations.
enum FileStore {
    /// NSFileCoordinator read; triggers download of cloud files.
    static func read(_ url: URL) throws -> Data {
        Data()
    }

    /// Coordinated replace (options .forReplacing). Inside the accessor everything uses the URL the coordinator hands
    /// over (newURL), never the one passed in: data -> temporary file in the item replacement directory for newURL ->
    /// FileManager.replaceItemAt(newURL, withItemAt:). When the replacement directory cannot be made or replaceItemAt
    /// throws (some file providers), data.write(to: newURL, options: .atomic) inside the same coordination.
    /// When `keepPrevious` is given and the file exists, it is copied there first. Creates the file when it does not
    /// exist. Returns the new modification date.
    @discardableResult static func replace(_ url: URL, with data: Data, keepPrevious: URL? = nil) throws -> Date {
        throw ZenithError.cannotSave(url, reason: "Saving is not available yet.")
    }

    /// Test hook: how replace finds its temporary folder. Default FileManager.url(for: .itemReplacementDirectory, ...).
    nonisolated(unsafe) static var replacementDirectory: (URL) throws -> URL = { url in
        try FileManager.default.url(for: .itemReplacementDirectory, in: .userDomainMask, appropriateFor: url, create: true)
    }

    /// NSFileVersion.unresolvedConflictVersionsOfItem(at:) not empty.
    static func hasUnresolvedConflicts(_ url: URL) -> Bool {
        false
    }

    /// Lowercase hex (CryptoKit).
    static func sha256(_ data: Data) -> String {
        ""
    }

    /// Coordinated, fails if destination exists.
    static func move(_ source: URL, to destination: URL) throws {
    }

    static func copy(_ source: URL, to destination: URL) throws {
    }

    static func remove(_ url: URL) throws {
    }

    /// With intermediate folders, no error if present.
    static func createFolder(_ url: URL) throws {
    }

    static func modificationDate(_ url: URL) -> Date? {
        nil
    }

    static func creationDate(_ url: URL) -> Date? {
        nil
    }

    static func exists(_ url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.path)
    }

    /// "Name.pdf", then "Name 2.pdf", "Name 3.pdf", ... (folders: "Name", "Name 2")
    static func uniqueURL(_ desired: URL) -> URL {
        desired
    }

    /// Trims; rejects "", "/", ":", leading ".".
    static func validatedName(_ name: String) throws -> String {
        name
    }
}
