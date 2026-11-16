import SwiftUI

@main struct ZenithApp: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
        WindowGroup(id: "note", for: NoteWindowValue.self) { $value in
            NoteWindow(value: value)
        }
    }
}

/// A window that shows one note; closing the note closes the window.
private struct NoteWindow: View {
    let value: NoteWindowValue?
    @Environment(\.dismissWindow) private var dismissWindow

    var body: some View {
        if let value {
            NoteScreen(value: value, launch: LaunchOptions.baseEditorLaunch(), onClose: { dismissWindow() })
                .tint(Folio.accent)
        } else {
            Folio.window.ignoresSafeArea()
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
        if let libraryRoot, SidecarLayout.isInside(url, root: libraryRoot) {
            libraryPath = LibraryStore(root: libraryRoot).relativePath(of: url)
            bookmark = nil
        } else {
            libraryPath = nil
            do {
                bookmark = try url.bookmarkData(options: [], includingResourceValuesForKeys: nil, relativeTo: nil)
            } catch {
                log.error("No bookmark for a note window: \(error.localizedDescription, privacy: .public)")
                bookmark = nil
            }
        }
    }

    /// Starts security-scoped access for a bookmark; nil when it fails.
    func resolve(libraryRoot: URL?) -> URL? {
        if let libraryPath {
            guard let libraryRoot else { return nil }
            let url = LibraryStore(root: libraryRoot).url(forRelativePath: libraryPath)
            return FileStore.exists(url) ? url : nil
        }
        guard let bookmark else { return nil }
        var isStale = false
        guard let url = try? URL(resolvingBookmarkData: bookmark, options: [], relativeTo: nil,
                                 bookmarkDataIsStale: &isStale) else { return nil }
        // Files inside the app's own container are not security-scoped (false here) and are still readable.
        _ = url.startAccessingSecurityScopedResource()
        return FileStore.exists(url) ? url : nil
    }
}

/// The note shown full screen in the main window (.fullScreenCover(item:) needs Identifiable; URL is not).
struct OpenNote: Identifiable, Hashable { let url: URL; var id: URL { url } }
