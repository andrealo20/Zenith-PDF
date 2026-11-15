import Foundation

/// Files / other apps / Inbox.
@MainActor enum OpenURLHandler {
    /// A PDF from Files (in place), from another app (Documents/Inbox: moved into the library root), or a
    /// Zenith note path. Returns the URL to open.
    static func handle(_ url: URL, library: LibraryModel) -> URL? {
        guard url.isFileURL, url.pathExtension.lowercased() == "pdf" else {
            log.error("Not a PDF to open: \(url.lastPathComponent, privacy: .public)")
            return nil
        }
        let root = library.location.root
        let inbox = library.location.localRoot.appendingPathComponent("Inbox", isDirectory: true)
        if SidecarLayout.isInside(url, root: inbox) {
            let destination = FileStore.uniqueURL(root.appendingPathComponent(url.lastPathComponent, isDirectory: false))
            do {
                try FileStore.move(url, to: destination)
            } catch {
                log.error("Inbox file not moved: \(error.localizedDescription, privacy: .public)")
                library.error = .cannotOpen(url)
                return nil
            }
            library.refresh()
            return destination.standardizedFileURL
        }
        // A note of the library, or a file opened in place from Files: NoteDocument.open starts its security scope.
        return url.standardizedFileURL
    }
}
