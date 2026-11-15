import PDFKit
import UIKit

final class ImageAnnotation: PDFAnnotation {
    private(set) var imageData: Data = Data()   // JPEG or PNG bytes kept in /ZenithImageData (base64)
    private var decoded: UIImage? = nil

    /// Bounds in PDF space.
    init(bounds: CGRect, imageData: Data) {
        super.init(bounds: bounds, forType: .stamp, withProperties: nil)
        self.imageData = imageData
        decoded = UIImage(data: imageData)
        if !setValue(imageData.base64EncodedString() as NSString, forAnnotationKey: ZenithKey.imageData) {
            log.error("PDFKit refused /ZenithImageData")
        }
    }

    /// As for InkAnnotation; imageData is decoded from value(forAnnotationKey: ZenithKey.imageData), else empty.
    override init(bounds: CGRect, forType annotationType: PDFAnnotationSubtype, withProperties properties: [AnyHashable: Any]?) {
        super.init(bounds: bounds, forType: annotationType, withProperties: properties)
        loadCarriedData()
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
        loadCarriedData()
    }

    /// Draws the picture in bounds.
    override func draw(with box: PDFDisplayBox, in context: CGContext) {
        guard let picture = decoded?.cgImage else {
            super.draw(with: box, in: context)
            return
        }
        // PDF space is y-up, as Core Graphics draws images, so the picture stands upright.
        context.saveGState()
        context.interpolationQuality = .high
        context.draw(picture, in: bounds)
        context.restoreGState()
    }

    /// Picture bytes for a placed image: longest side at most 2048 px; PNG when it has transparency, else JPEG 0.85.
    static func preparedData(for image: UIImage) -> Data? {
        let pixels = CGSize(width: image.size.width * image.scale, height: image.size.height * image.scale)
        guard pixels.width >= 1, pixels.height >= 1 else { return nil }
        let factor = min(1, 2048 / max(pixels.width, pixels.height))
        let target = CGSize(width: max(1, (pixels.width * factor).rounded()), height: max(1, (pixels.height * factor).rounded()))
        let alpha = image.cgImage?.alphaInfo ?? CGImageAlphaInfo.none
        let withAlpha: [CGImageAlphaInfo] = [.first, .last, .premultipliedFirst, .premultipliedLast, .alphaOnly]
        let transparent = withAlpha.contains(alpha)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        format.opaque = !transparent
        let renderer = UIGraphicsImageRenderer(size: target, format: format)
        let draw: (UIGraphicsImageRendererContext) -> Void = { _ in
            image.draw(in: CGRect(origin: .zero, size: target))
        }
        return transparent ? renderer.pngData(actions: draw) : renderer.jpegData(withCompressionQuality: 0.85, actions: draw)
    }

    /// Decoded once and cached.
    var image: UIImage? { decoded }

    private func loadCarriedData() {
        guard let text = value(forAnnotationKey: ZenithKey.imageData) as? String,
              let data = Data(base64Encoded: text) else { return }
        imageData = data
        decoded = UIImage(data: data)
    }
}
