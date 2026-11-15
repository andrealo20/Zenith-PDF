import Foundation

/// Files / other apps / Inbox.
@MainActor enum OpenURLHandler {
    /// A PDF from Files (in place), from another app (Documents/Inbox: moved into the library root), or a
    /// Zenith note path. Returns the URL to open.
    static func handle(_ url: URL, library: LibraryModel) -> URL? {
        nil
    }
}
