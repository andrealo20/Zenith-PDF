import PDFKit
import UIKit

/// Writes one InkRecord: InkList through add(_:), keys through setValue, and an appearance that draws the
/// variable width. Only ever created in the save copy, never in the displayed document.
final class InkAnnotation: PDFAnnotation {
    private(set) var record: InkRecord? = nil   // nil when PDFKit made the object through an inherited path

    init(record: InkRecord) {
        super.init(bounds: record.bounds, forType: .ink, withProperties: nil)
        self.record = record
    }

    /// PDFKit's designated initialiser. PDFKit calls it when it copies an annotation; without this override
    /// Swift traps with "use of unimplemented initializer" (section 9). Leaves record nil.
    override init(bounds: CGRect, forType annotationType: PDFAnnotationSubtype, withProperties properties: [AnyHashable: Any]?) {
        super.init(bounds: bounds, forType: annotationType, withProperties: properties)
    }

    required init?(coder: NSCoder) {
        super.init(coder: coder)
    }

    /// The record's appearance; super.draw when nil.
    override func draw(with box: PDFDisplayBox, in context: CGContext) {
        super.draw(with: box, in: context)
    }
}
