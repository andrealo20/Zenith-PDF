import SwiftUI

@main struct ZenithApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
        WindowGroup(id: "note", for: NoteWindowValue.self) { $value in
            if let value {
                NoteScreen(value: value, launch: EditorLaunchState(), onClose: {})
            }
        }
    }
}

/// What a note window is restored from: the library-relative path for a note in the library, a bookmark for a note
/// opened in place from Files (its security-scoped access is gone after a relaunch, the bookmark brings it back).
struct NoteWindowValue: Codable, Hashable {
    var libraryPath: String?
    var bookmark: Data?

    /// libraryPath when the note is inside libraryRoot, else url.bookmarkData().
    init(url: URL, libraryRoot: URL?) {
        libraryPath = nil
        bookmark = nil
    }

    /// Starts security-scoped access for a bookmark; nil when it fails.
    func resolve(libraryRoot: URL?) -> URL? {
        nil
    }
}

/// The note shown full screen in the main window (.fullScreenCover(item:) needs Identifiable; URL is not).
struct OpenNote: Identifiable, Hashable { let url: URL; var id: URL { url } }
