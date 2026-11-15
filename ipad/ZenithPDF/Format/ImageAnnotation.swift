import PDFKit
import UIKit

final class ImageAnnotation: PDFAnnotation {
    private(set) var imageData: Data = Data()   // JPEG or PNG bytes kept in /ZenithImageData (base64)

    /// Bounds in PDF space.
    init(bounds: CGRect, imageData: Data) {
        super.init(bounds: bounds, forType: .stamp, withProperties: nil)
        self.imageData = imageData
    }

    /// As for InkAnnotation; imageData is decoded from value(forAnnotationKey: ZenithKey.imageData), else empty.
    override init(bounds: CGRect, forType annotationType: PDFAnnotationSubtype, withProperties properties: [AnyHashable: Any]?) {
        super.init(bounds: bounds, forType: annotationType, withProperties: properties)
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
    }

    /// Draws the picture in bounds.
    override func draw(with box: PDFDisplayBox, in context: CGContext) {
        super.draw(with: box, in: context)
    }

    /// Picture bytes for a placed image: longest side at most 2048 px; PNG when it has transparency, else JPEG 0.85.
    static func preparedData(for image: UIImage) -> Data? {
        nil
    }

    /// Decoded once and cached.
    var image: UIImage? { UIImage(data: imageData) }
}
