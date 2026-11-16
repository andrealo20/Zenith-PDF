import CoreGraphics
import PDFKit

/// Port of desktop core/geometry.py. Display space: points, origin at the top-left of the page as
/// shown, page rotation applied. PDF space: the page's user space (what PDFKit calls page space,
/// what /Rect and /InkList use).
struct PageGeometry: Equatable, Sendable {
    let box: CGRect          // visible box in PDF space: crop box clipped to media box (media box if empty)
    let rotation: Int        // clockwise: 0, 90, 180 or 270

    init(box: CGRect, rotation: Int) {
        self.box = box.standardized
        self.rotation = PageGeometry.normalized(rotation)
    }

    init(page: PDFPage) {
        let media = page.bounds(for: .mediaBox).standardized
        let crop = page.bounds(for: .cropBox)
        self.init(box: PageGeometry.visibleBox(media: media, crop: crop), rotation: page.rotation)
    }

    var displaySize: CGSize {
        rotation == 90 || rotation == 270
            ? CGSize(width: box.height, height: box.width)
            : CGSize(width: box.width, height: box.height)
    }

    func toDisplay(_ p: CGPoint) -> CGPoint {
        let left = box.minX, bottom = box.minY, right = box.maxX, top = box.maxY
        switch rotation {
        case 90: return CGPoint(x: p.y - bottom, y: p.x - left)
        case 180: return CGPoint(x: right - p.x, y: p.y - bottom)
        case 270: return CGPoint(x: top - p.y, y: right - p.x)
        default: return CGPoint(x: p.x - left, y: top - p.y)
        }
    }

    func toPDF(_ p: CGPoint) -> CGPoint {
        let left = box.minX, bottom = box.minY, right = box.maxX, top = box.maxY
        switch rotation {
        case 90: return CGPoint(x: p.y + left, y: p.x + bottom)
        case 180: return CGPoint(x: right - p.x, y: p.y + bottom)
        case 270: return CGPoint(x: right - p.y, y: top - p.x)
        default: return CGPoint(x: p.x + left, y: top - p.y)
        }
    }

    func rectToDisplay(_ r: CGRect) -> CGRect {
        let r = r.standardized
        return PageGeometry.spanning(toDisplay(CGPoint(x: r.minX, y: r.minY)), toDisplay(CGPoint(x: r.maxX, y: r.maxY)))
    }

    func rectToPDF(_ r: CGRect) -> CGRect {
        let r = r.standardized
        return PageGeometry.spanning(toPDF(CGPoint(x: r.minX, y: r.minY)), toPDF(CGPoint(x: r.maxX, y: r.maxY)))
    }

    /// The area the desktop displays: the crop box clipped to the media box, the media box when that is empty.
    private static func visibleBox(media: CGRect, crop: CGRect) -> CGRect {
        if crop.isNull || crop.isEmpty || crop.isInfinite { return media }
        let crop = crop.standardized
        let left = max(media.minX, crop.minX)
        let bottom = max(media.minY, crop.minY)
        let right = min(media.maxX, crop.maxX)
        let top = min(media.maxY, crop.maxY)
        if right <= left || top <= bottom { return media }
        return CGRect(x: left, y: bottom, width: right - left, height: top - bottom)
    }

    /// Any multiple of 90 degrees, negative or above a full turn, as 0, 90, 180 or 270.
    private static func normalized(_ rotation: Int) -> Int {
        let quarter = Int((Double(rotation) / 90).rounded())
        return ((quarter % 4) + 4) % 4 * 90
    }

    private static func spanning(_ a: CGPoint, _ b: CGPoint) -> CGRect {
        CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(b.x - a.x), height: abs(b.y - a.y))
    }
}
