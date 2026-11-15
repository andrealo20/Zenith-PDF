import UIKit

/// First-page thumbnails on disk.
actor ThumbnailCache {
    static let shared = ThumbnailCache()

    /// First page with its annotations, cached in Caches/Thumbnails/<SHA-256 of path>-<modified>.png.
    func thumbnail(for note: URL, modified: Date, width: CGFloat) async -> UIImage? {
        nil
    }

    /// From the main actor: Task { await ThumbnailCache.shared.invalidate(url) }
    func invalidate(_ note: URL) {
    }
}
