import PDFKit
import UIKit

/// Writes one InkRecord: InkList through add(_:), keys through setValue, and an appearance that draws the
/// variable width. Only ever created in the save copy, never in the displayed document.
final class InkAnnotation: PDFAnnotation {
    private(set) var record: InkRecord? = nil   // nil when PDFKit made the object through an inherited path

    init(record: InkRecord) {
        super.init(bounds: record.bounds, forType: .ink, withProperties: nil)
        self.record = record
        // The colour first: PDFKit may derive /CA from its alpha, which the explicit /CA below overwrites.
        color = record.color.uiColor(alpha: record.opacity)
        let origin = bounds.origin
        for points in record.paths {
            guard let first = points.first else { continue }
            // Paths are relative to the annotation's bounds (pitfall P11).
            let path = UIBezierPath()
            path.move(to: CGPoint(x: first.x - origin.x, y: first.y - origin.y))
            for point in points.dropFirst() {
                path.addLine(to: CGPoint(x: point.x - origin.x, y: point.y - origin.y))
            }
            add(path)
        }
        let border = PDFBorder()
        border.lineWidth = record.maxWidth
        self.border = border
        write(NSNumber(value: Double(record.opacity)), WrittenKeys.opacity)
        let widths = record.widths.map { $0.map { NSNumber(value: Double($0)) } } as NSArray
        write(widths, ZenithKey.widths)
        if record.highlighter { write(NSNumber(value: true), ZenithKey.highlighter) }
        write(record.pencilKit as NSString, ZenithKey.pencilKit)
        write(record.check.string as NSString, ZenithKey.inkCheck)
        write(record.identifier as NSString, WrittenKeys.identifier)
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
        guard let record else {
            super.draw(with: box, in: context)
            return
        }
        context.saveGState()
        context.setLineCap(.round)
        context.setLineJoin(.round)
        context.setAlpha(record.opacity)
        if record.highlighter { context.setBlendMode(.multiply) }
        // DeviceRGB, as the desktop writes ink: no ICC profile resource in each of a thousand appearances.
        let rgb = record.color
        context.setStrokeColor(red: rgb.r, green: rgb.g, blue: rgb.b, alpha: 1)
        for (points, widths) in zip(record.paths, record.widths) where points.count > 1 && widths.count == points.count {
            // Segments of one width go out as one path: far fewer operators in the file and a faster save.
            var runWidth: CGFloat = -1
            for i in 1..<points.count {
                let width = (widths[i - 1] + widths[i]) / 2
                if abs(width - runWidth) > 0.005 {
                    if runWidth >= 0 { context.strokePath() }
                    runWidth = width
                    context.setLineWidth(width)
                    context.move(to: points[i - 1])
                }
                context.addLine(to: points[i])
            }
            context.strokePath()
        }
        context.restoreGState()
    }

    private func write(_ value: Any, _ key: PDFAnnotationKey) {
        // Logged, not asserted: one refused key must not take the hosted test run down.
        if !setValue(value, forAnnotationKey: key) {
            log.error("PDFKit refused \(key.rawValue, privacy: .public)")
        }
    }
}
