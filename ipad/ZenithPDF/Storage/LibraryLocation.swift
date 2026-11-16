import Foundation
import Observation

/// Local folder or bookmarked folder, first-launch choice.
@MainActor @Observable final class LibraryLocation {
    enum Kind: Equatable { case local; case folder(name: String) }

    static let shared = LibraryLocation()

    private(set) var root: URL
    private(set) var kind: Kind = .local
    private(set) var problem: String? = nil       // set when the saved folder could not be opened

    private var choiceMade: Bool
    private var isTemporary = false
    private let defaults: UserDefaults
    /// The folder whose security-scoped access is open; kept for the app's lifetime, closed only when replaced.
    @ObservationIgnored private var scopedFolder: URL?

    /// Tests pass their own defaults so a run never changes the app's saved choice.
    init(defaults: UserDefaults = .standard) {
        self.defaults = defaults
        root = LibraryLocation.documentsFolder()
        choiceMade = defaults.bool(forKey: SettingKey.libraryChoiceMade)
    }

    /// The app's Documents folder.
    var localRoot: URL { LibraryLocation.documentsFolder() }

    /// true until SettingKey.libraryChoiceMade is set (fresh install).
    var needsChoice: Bool { !isTemporary && !choiceMade }

    /// Launch: resolve the bookmark, start access, else local.
    func resolve() {
        guard let data = defaults.data(forKey: SettingKey.libraryBookmark) else {
            setLocal(problem: nil)
            return
        }
        do {
            var isStale = false
            let url = try URL(resolvingBookmarkData: data, options: [], relativeTo: nil, bookmarkDataIsStale: &isStale)
            guard open(url) else {
                setLocal(problem: tr("The folder could not be opened. Notes are being kept on this iPad."))
                return
            }
            if isStale, let fresh = try? url.bookmarkData(options: .minimalBookmark, includingResourceValuesForKeys: nil,
                                                          relativeTo: nil) {
                defaults.set(fresh, forKey: SettingKey.libraryBookmark)
            }
            setFolder(url)
        } catch {
            log.error("Library bookmark not resolved: \(error.localizedDescription, privacy: .public)")
            setLocal(problem: tr("The folder could not be opened. Notes are being kept on this iPad."))
        }
    }

    /// From the folder picker: start access, save .minimalBookmark, mark the choice.
    func choose(folder url: URL) throws {
        guard open(url) else { throw ZenithError.accessDenied(url) }
        let bookmark: Data
        do {
            bookmark = try url.bookmarkData(options: .minimalBookmark, includingResourceValuesForKeys: nil, relativeTo: nil)
        } catch {
            throw ZenithError.accessDenied(url)
        }
        defaults.set(bookmark, forKey: SettingKey.libraryBookmark)
        markChoice()
        setFolder(url)
    }

    /// The person chose the app's folder: local root, mark the choice.
    func useLocal() {
        defaults.removeObject(forKey: SettingKey.libraryBookmark)
        markChoice()
        setLocal(problem: nil)
    }

    /// Demo and tests: no bookmark change, no choice asked.
    func useTemporaryRoot(_ url: URL) {
        isTemporary = true
        root = url
        kind = .local
        problem = nil
    }

    // MARK: - Private

    /// Starts security-scoped access to `url`, closing the previous folder's. A folder inside the app's own container
    /// is not security-scoped (start returns false) but is still usable, so reachability decides.
    private func open(_ url: URL) -> Bool {
        if let scopedFolder, scopedFolder.standardizedFileURL.path == url.standardizedFileURL.path { return true }
        let started = url.startAccessingSecurityScopedResource()
        var directory: ObjCBool = false
        guard FileManager.default.fileExists(atPath: url.path, isDirectory: &directory), directory.boolValue else {
            if started { url.stopAccessingSecurityScopedResource() }
            return false
        }
        scopedFolder?.stopAccessingSecurityScopedResource()
        scopedFolder = started ? url : nil
        return true
    }

    private func setFolder(_ url: URL) {
        isTemporary = false
        root = url
        kind = .folder(name: FileManager.default.displayName(atPath: url.path))
        problem = nil
    }

    private func setLocal(problem: String?) {
        scopedFolder?.stopAccessingSecurityScopedResource()
        scopedFolder = nil
        isTemporary = false
        root = localRoot
        kind = .local
        self.problem = problem
    }

    private func markChoice() {
        defaults.set(true, forKey: SettingKey.libraryChoiceMade)
        choiceMade = true
    }

    private static func documentsFolder() -> URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
    }
}
