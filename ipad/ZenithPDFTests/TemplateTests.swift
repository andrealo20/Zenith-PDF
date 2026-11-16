import PDFKit
import UIKit
import XCTest
@testable import ZenithPDF

/// Counts and positions are the desktop's: core/pages.py template_content on the same sizes gives 33 lined rules
/// on A4 (31 on Letter), 42 + 59 grid lines and 2478 dots on A4 (43 + 55 and 2365 on Letter).
final class TemplateTests: XCTestCase {
    private let mm = TemplateGeometry.millimetre
    private let a4 = PaperSize.a4.points
    private let letter = PaperSize.letter.points

    func testLinedMatchesDesktop() {
        let marks = TemplateGeometry.marks(for: .lined, size: a4)
        XCTAssertEqual(marks.rules.count, 33)
        XCTAssertEqual(TemplateGeometry.marks(for: .lined, size: letter).rules.count, 31)
        XCTAssertEqual(marks.ruleWidth, 0.5)
        guard let first = marks.rules.first, let last = marks.rules.last else { return XCTFail("no rules") }
        XCTAssertEqual(first.start.y, 25 * mm, accuracy: 0.001)
        XCTAssertEqual(first.start.x, 12 * mm, accuracy: 0.001)
        XCTAssertEqual(first.end.x, a4.width - 12 * mm, accuracy: 0.001)
        XCTAssertEqual(first.start.y, first.end.y)
        for (above, below) in zip(marks.rules, marks.rules.dropFirst()) {
            XCTAssertEqual(below.start.y - above.start.y, 8 * mm, accuracy: 0.001)
        }
        XCTAssertLessThan(last.start.y, a4.height - 12 * mm, "the last rule stays more than 12 mm above the bottom")
        XCTAssertGreaterThanOrEqual(last.start.y + 8 * mm, a4.height - 12 * mm, "no rule is missing at the bottom")
        // The desktop's first rule, in PDF space: 771.024.
        XCTAssertEqual(a4.height - first.start.y, 771.024, accuracy: 0.001)
        XCTAssertEqual(marks.margin, RuleSegment(start: CGPoint(x: 25 * mm, y: 0), end: CGPoint(x: 25 * mm, y: a4.height)))
        XCTAssertEqual(marks.marginWidth, 0.6)
        XCTAssertTrue(marks.dots.isEmpty)
    }

    func testCollegeRuledSpacing() {
        let marks = TemplateGeometry.marks(for: .collegeRuled, size: a4)
        XCTAssertEqual(marks.rules.count, 37)
        guard let first = marks.rules.first, let last = marks.rules.last else { return XCTFail("no rules") }
        XCTAssertEqual(first.start.y, 25 * mm, accuracy: 0.001)
        for (above, below) in zip(marks.rules, marks.rules.dropFirst()) {
            XCTAssertEqual(below.start.y - above.start.y, 20.25, accuracy: 0.001)
        }
        XCTAssertLessThan(last.start.y, a4.height - 12 * mm)
        XCTAssertGreaterThanOrEqual(last.start.y + 20.25, a4.height - 12 * mm)
        XCTAssertEqual(marks.ruleWidth, 0.5)
        XCTAssertNotNil(marks.margin)
        XCTAssertEqual(TemplateGeometry.marks(for: .collegeRuled, size: letter).rules.count, 34)
    }

    func testGridAndDots() {
        let grid = TemplateGeometry.marks(for: .grid, size: a4)
        let vertical = grid.rules.filter { $0.start.x == $0.end.x }
        let horizontal = grid.rules.filter { $0.start.y == $0.end.y }
        XCTAssertEqual(vertical.count, 42)
        XCTAssertEqual(horizontal.count, 59)
        XCTAssertEqual(grid.rules.count, 101)
        XCTAssertEqual(grid.ruleWidth, 0.35)
        XCTAssertNil(grid.margin)
        XCTAssertEqual(vertical.first?.start.x ?? 0, 5 * mm, accuracy: 0.001)
        XCTAssertEqual(vertical.first?.end.y ?? 0, a4.height, accuracy: 0.001)
        // Horizontal lines are counted from the bottom, as the desktop draws them: the lowest is 5 mm up.
        XCTAssertEqual(horizontal.first?.start.y ?? 0, a4.height - 5 * mm, accuracy: 0.001)
        XCTAssertEqual(TemplateGeometry.marks(for: .grid, size: letter).rules.count, 98)

        let dotted = TemplateGeometry.marks(for: .dotted, size: a4)
        XCTAssertEqual(dotted.dots.count, 2478)
        XCTAssertEqual(dotted.dotDiameter, 1.3)
        XCTAssertTrue(dotted.rules.isEmpty)
        XCTAssertEqual(dotted.dots.first?.x ?? 0, 5 * mm, accuracy: 0.001)
        XCTAssertEqual(dotted.dots.first?.y ?? 0, a4.height - 5 * mm, accuracy: 0.001)
        XCTAssertEqual(TemplateGeometry.marks(for: .dotted, size: letter).dots.count, 2365)

        let blank = TemplateGeometry.marks(for: .blank, size: a4)
        XCTAssertTrue(blank.rules.isEmpty && blank.dots.isEmpty && blank.margin == nil)
    }

    func testPageSizes() {
        for size in PaperSize.allCases {
            let template = PaperTemplate(pattern: .lined, color: .white, size: size)
            guard let document = PDFDocument(data: TemplateRenderer.pdfData(for: template, pageCount: 3)) else {
                return XCTFail("template PDF for \(size) does not open")
            }
            XCTAssertEqual(document.pageCount, 3)
            guard let page = document.page(at: 0) else { return XCTFail("no page") }
            let box = page.bounds(for: .mediaBox)
            XCTAssertEqual(box.width, size.points.width, accuracy: 0.01)
            XCTAssertEqual(box.height, size.points.height, accuracy: 0.01)
        }
    }

    @MainActor func testDarkPaperFill() {
        let template = PaperTemplate(pattern: .blank, color: .dark, size: .a4)
        let page = TemplateRenderer.page(for: template)
        XCTAssertEqual(page.bounds(for: .mediaBox).width, a4.width, accuracy: 0.01)
        let image = page.thumbnail(of: CGSize(width: 200, height: 283), for: .mediaBox)
        guard let centre = TemplateTests.pixel(of: image, at: CGPoint(x: 0.5, y: 0.5)) else { return XCTFail("no pixels") }
        let fill = PaperColor.dark.fill
        XCTAssertEqual(centre.r, fill.r, accuracy: 0.03)
        XCTAssertEqual(centre.g, fill.g, accuracy: 0.03)
        XCTAssertEqual(centre.b, fill.b, accuracy: 0.03)

        // White paper is painted too, so a viewer in dark mode still shows a white sheet.
        let preview = TemplateRenderer.preview(for: PaperTemplate(pattern: .blank, color: .cream, size: .a4), width: 100)
        guard let cream = TemplateTests.pixel(of: preview, at: CGPoint(x: 0.5, y: 0.5)) else { return XCTFail("no pixels") }
        XCTAssertEqual(cream.b, PaperColor.cream.fill.b, accuracy: 0.03)
        XCTAssertEqual(preview.size.width, 100, accuracy: 0.5)
    }

    /// The colour at a point given as a fraction of the image's width and height, read from an sRGB copy.
    static func pixel(of image: UIImage, at fraction: CGPoint) -> RGB? {
        guard let cgImage = image.cgImage else { return nil }
        let width = cgImage.width, height = cgImage.height
        var bytes = [UInt8](repeating: 0, count: width * height * 4)
        let drawn: Bool = bytes.withUnsafeMutableBytes { buffer in
            guard let space = CGColorSpace(name: CGColorSpace.sRGB),
                  let context = CGContext(data: buffer.baseAddress, width: width, height: height, bitsPerComponent: 8,
                                          bytesPerRow: width * 4, space: space,
                                          bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return false }
            context.draw(cgImage, in: CGRect(x: 0, y: 0, width: width, height: height))
            return true
        }
        guard drawn else { return nil }
        // Bitmap memory starts at the top row.
        let x = min(max(Int(fraction.x * CGFloat(width)), 0), width - 1)
        let y = min(max(Int(fraction.y * CGFloat(height)), 0), height - 1)
        let offset = (y * width + x) * 4
        return RGB(r: CGFloat(bytes[offset]) / 255, g: CGFloat(bytes[offset + 1]) / 255, b: CGFloat(bytes[offset + 2]) / 255)
    }
}
