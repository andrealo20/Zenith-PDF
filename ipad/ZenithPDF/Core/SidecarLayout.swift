import CryptoKit
import Foundation

enum SidecarLayout {
    static let folderName = ".zenith"          // in every folder that holds notes
    static let trashName = ".zenith-trash"     // at the library root

    /// <folder of the note>/.zenith/<note file name>/ when the note is inside libraryRoot;
    /// <Application Support>/External/<first 16 hex of SHA-256 of the path>/ otherwise.
    static func folder(forNote note: URL, libraryRoot: URL?) -> URL {
        let note = note.standardizedFileURL
        if let root = libraryRoot, isInside(note, root: root) {
            return note.deletingLastPathComponent()
                .appendingPathComponent(folderName, isDirectory: true)
                .appendingPathComponent(note.lastPathComponent, isDirectory: true)
        }
        let digest = SHA256.hash(data: Data(comparablePath(note).utf8))
        let hex = digest.map { String(format: "%02x", $0) }.joined()
        return applicationSupport()
            .appendingPathComponent("External", isDirectory: true)
            .appendingPathComponent(String(hex.prefix(16)), isDirectory: true)
    }

    static func metadata(forNote note: URL, libraryRoot: URL?) -> URL {
        folder(forNote: note, libraryRoot: libraryRoot).appendingPathComponent("meta.json", isDirectory: false)
    }

    static func searchIndex(forNote note: URL, libraryRoot: URL?) -> URL {
        folder(forNote: note, libraryRoot: libraryRoot).appendingPathComponent("search.json", isDirectory: false)
    }

    static func recordings(forNote note: URL, libraryRoot: URL?) -> URL {
        folder(forNote: note, libraryRoot: libraryRoot).appendingPathComponent("recordings.json", isDirectory: false)
    }

    static func audioFolder(forNote note: URL, libraryRoot: URL?) -> URL {
        folder(forNote: note, libraryRoot: libraryRoot).appendingPathComponent("audio", isDirectory: true)
    }

    static func previousVersion(forNote note: URL, libraryRoot: URL?) -> URL {
        folder(forNote: note, libraryRoot: libraryRoot).appendingPathComponent("previous.pdf", isDirectory: false)
    }

    /// Last path component starts with ".".
    static func isHidden(_ url: URL) -> Bool {
        url.lastPathComponent.hasPrefix(".")
    }

    /// Compares resolved, standardized paths component by component; a folder counts as inside itself.
    static func isInside(_ url: URL, root: URL) -> Bool {
        let path = comparablePath(url).split(separator: "/")
        let base = comparablePath(root).split(separator: "/")
        return path.count >= base.count && Array(path.prefix(base.count)) == base
    }

    /// The same string for every spelling of one file: standardized, symlinks resolved, and "/private/var"
    /// read as "/var" (pitfall P12), whether or not the file exists yet.
    private static func comparablePath(_ url: URL) -> String {
        var path = url.standardizedFileURL.resolvingSymlinksInPath().path
        for prefix in ["/private/var/", "/private/tmp/", "/private/etc/"] where path.hasPrefix(prefix) {
            path = String(path.dropFirst("/private".count))
        }
        if path.count > 1 && path.hasSuffix("/") { path.removeLast() }
        return path
    }

    private static func applicationSupport() -> URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
    }
}
