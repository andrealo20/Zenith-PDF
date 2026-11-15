import PDFKit
import UIKit

/// First-page thumbnails on disk.
actor ThumbnailCache {
    static let shared = ThumbnailCache()

    private let folder: URL
    private static let scale: CGFloat = 2

    /// `folder` is for tests; the app keeps thumbnails in Caches/Thumbnails.
    init(folder: URL? = nil) {
        self.folder = folder
            ?? (FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first
                ?? FileManager.default.temporaryDirectory).appendingPathComponent("Thumbnails", isDirectory: true)
    }

    /// First page with its annotations, cached in Caches/Thumbnails/<SHA-256 of path>-<modified>.png.
    func thumbnail(for note: URL, modified: Date, width: CGFloat) async -> UIImage? {
        guard width > 0, width.isFinite else { return nil }
        let key = Self.key(for: note)
        let file = folder.appendingPathComponent("\(key)-\(Self.stamp(modified)).png", isDirectory: false)
        if let data = try? Data(contentsOf: file), let cached = UIImage(data: data, scale: Self.scale),
           cached.size.width >= width - 1 {
            return cached
        }
        guard let data = try? FileStore.read(note), let document = PDFDocument(data: data),
              let page = document.page(at: 0) else { return nil }
        page.displaysAnnotations = true
        let box = page.bounds(for: .cropBox).size
        let quarterTurn = (page.rotation / 90) % 2 != 0
        let shown = quarterTurn ? CGSize(width: box.height, height: box.width) : box
        guard shown.width > 0, shown.height > 0 else { return nil }
        // Asked at the page's own aspect ratio, so the result has no margins whether PDFKit fits or fills, then drawn
        // once more so the size and scale are ours whatever scale PDFKit renders at.
        let target = CGSize(width: width, height: max((width * shown.height / shown.width).rounded(), 1))
        let rendered = page.thumbnail(of: CGSize(width: target.width * Self.scale, height: target.height * Self.scale),
                                      for: .cropBox)
        let format = UIGraphicsImageRendererFormat()
        format.scale = Self.scale
        format.opaque = false
        let image = UIGraphicsImageRenderer(size: target, format: format).image { _ in
            rendered.draw(in: CGRect(origin: .zero, size: target))
        }
        removeFiles(withPrefix: key + "-")
        if let png = image.pngData() {
            do {
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                try png.write(to: file, options: .atomic)
            } catch {
                log.error("Thumbnail not cached: \(error.localizedDescription, privacy: .public)")
            }
        }
        return image
    }

    /// From the main actor: Task { await ThumbnailCache.shared.invalidate(url) }
    func invalidate(_ note: URL) {
        removeFiles(withPrefix: Self.key(for: note) + "-")
    }

    // MARK: - Private

    private func removeFiles(withPrefix prefix: String) {
        let entries = (try? FileManager.default.contentsOfDirectory(at: folder, includingPropertiesForKeys: nil)) ?? []
        for entry in entries where entry.lastPathComponent.hasPrefix(prefix) {
            try? FileManager.default.removeItem(at: entry)
        }
    }

    private static func key(for note: URL) -> String {
        FileStore.sha256(Data(note.standardizedFileURL.resolvingSymlinksInPath().path.utf8))
    }

    /// Milliseconds since 1970: two saves within one second still get different names.
    private static func stamp(_ date: Date) -> String {
        String(Int64((date.timeIntervalSince1970 * 1000).rounded()))
    }
}
