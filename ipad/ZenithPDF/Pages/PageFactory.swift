import PDFKit
import UIKit

/// Pages and documents from images.
enum PageFactory {
    /// Longest side of the picture a page keeps, in pixels: a camera photo at full size would make a page of
    /// tens of megabytes.
    private static let maxPixels: CGFloat = 2048

    /// PDFPage(image:) [R26]. The page has the picture's proportions and fits inside an A4 sheet.
    @MainActor static func page(from image: UIImage) -> PDFPage? {
        guard let upright = prepared(image) else { return nil }
        let a4 = PaperSize.a4.points
        let fit = min(a4.width / upright.size.width, a4.height / upright.size.height)
        let box = CGRect(x: 0, y: 0, width: (upright.size.width * fit).rounded(), height: (upright.size.height * fit).rounded())
        let options: [PDFPage.ImageInitializationOption: Any] = [
            .mediaBox: NSValue(cgRect: box),
            .upscaleIfSmaller: NSNumber(value: true),
            .compressionQuality: NSNumber(value: 0.85),
        ]
        return PDFPage(image: upright, options: options)
    }

    /// A new note from photos or scans.
    @MainActor static func pdfData(from images: [UIImage]) -> Data? {
        let document = PDFDocument()
        for image in images {
            guard let page = page(from: image) else { continue }
            document.insert(page, at: document.pageCount)
        }
        guard document.pageCount > 0 else { return nil }
        return document.dataRepresentation()
    }

    /// Drawn again upright (PDFKit is not relied on to honour imageOrientation) and no larger than maxPixels.
    @MainActor private static func prepared(_ image: UIImage) -> UIImage? {
        let pixelWidth = image.size.width * image.scale
        let pixelHeight = image.size.height * image.scale
        guard pixelWidth >= 1, pixelHeight >= 1 else { return nil }
        let shrink = min(1, maxPixels / max(pixelWidth, pixelHeight))
        let target = CGSize(width: (pixelWidth * shrink).rounded(), height: (pixelHeight * shrink).rounded())
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = !hasAlpha(image)
        let renderer = UIGraphicsImageRenderer(size: target, format: format)
        return renderer.image { _ in
            image.draw(in: CGRect(origin: .zero, size: target))
        }
    }

    private static func hasAlpha(_ image: UIImage) -> Bool {
        guard let info = image.cgImage?.alphaInfo else { return false }
        return !(info == .none || info == .noneSkipFirst || info == .noneSkipLast)
    }
}
