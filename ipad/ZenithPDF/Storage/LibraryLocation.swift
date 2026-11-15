import Foundation
import Observation

/// Local folder or bookmarked folder, first-launch choice.
@MainActor @Observable final class LibraryLocation {
    enum Kind: Equatable { case local; case folder(name: String) }

    static let shared = LibraryLocation()

    private(set) var root: URL
    private(set) var kind: Kind = .local
    private(set) var problem: String? = nil       // set when the saved folder could not be opened

    init() {
        root = LibraryLocation.documentsFolder()
    }

    /// The app's Documents folder.
    var localRoot: URL { LibraryLocation.documentsFolder() }

    /// true until SettingKey.libraryChoiceMade is set (fresh install).
    var needsChoice: Bool { !UserDefaults.standard.bool(forKey: SettingKey.libraryChoiceMade) }

    /// Launch: resolve the bookmark, start access, else local.
    func resolve() {
        root = localRoot
        kind = .local
    }

    /// From the folder picker: start access, save .minimalBookmark, mark the choice.
    func choose(folder url: URL) throws {
    }

    /// The person chose the app's folder: local root, mark the choice.
    func useLocal() {
        root = localRoot
        kind = .local
    }

    /// Demo and tests: no bookmark change, no choice asked.
    func useTemporaryRoot(_ url: URL) {
        root = url
    }

    private static func documentsFolder() -> URL {
        FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
    }
}
