import PDFKit
import PencilKit
import UIKit

/// Page + ink thumbnails.
@MainActor enum PageThumbnailer {
    /// The page as shown (rotation applied) with its ink on top, `width` points wide.
    static func thumbnail(page: PDFPage, drawing: PKDrawing, geometry: PageGeometry, width: CGFloat) -> UIImage {
        let display = geometry.displaySize
        guard display.width > 0, display.height > 0 else { return UIImage() }
        let scale = max(width, 1) / display.width
        let size = CGSize(width: display.width * scale, height: display.height * scale)
        let format = UIGraphicsImageRendererFormat()
        format.opaque = true
        let pageImage = page.thumbnail(of: CGSize(width: size.width * format.scale, height: size.height * format.scale),
                                       for: .cropBox)
        var inkImage: UIImage?
        if !drawing.strokes.isEmpty {
            // Pitfall P7: PencilKit would turn black ink white in a dark appearance.
            UITraitCollection(userInterfaceStyle: .light).performAsCurrent {
                inkImage = drawing.image(from: CGRect(origin: .zero, size: display), scale: scale * format.scale)
            }
        }
        let renderer = UIGraphicsImageRenderer(size: size, format: format)
        return renderer.image { context in
            let rect = CGRect(origin: .zero, size: size)
            UIColor.white.setFill()
            context.fill(rect)
            pageImage.draw(in: rect)
            inkImage?.draw(in: rect)
        }
    }
}
