import CoreGraphics
import Foundation
import PDFKit

/// Annotation dictionaries as the file has them, read with CGPDF (Core Graphics' parser), independent of
/// what PDFKit keeps in memory. Used by NoteReader at open time and by tests to check what was written.
struct RawAnnotation {
    let subtype: String               // "Ink", "FreeText", "Stamp", ...
    let identifier: String?           // /NM
    let rect: CGRect                  // /Rect, PDF space, standardized
    let flags: Int                    // /F
    let color: [CGFloat]?             // /C as written (0, 1, 3 or 4 numbers)
    let opacity: CGFloat?             // /CA
    let borderWidth: CGFloat?         // /BS /W, else /Border[2]
    let inkList: [[CGPoint]]?         // PDF space, as written
    let widths: [[CGFloat]]?          // /ZenithWidths, only when every element is an array of numbers
    let highlighter: Bool             // /ZenithHighlighter true or a nonzero number
    let pencilKit: String?            // /ZenithPencilKit
    let inkCheck: String?             // /ZenithInkCheck
    let imageData: String?            // /ZenithImageData
    let contents: String?             // /Contents
    let defaultAppearance: String?    // /DA
    let defaultStyle: String?         // /DS
    let rectDifferences: [CGFloat]?   // /RD
    let appearanceData: Data?         // decoded /AP /N stream content, nil when absent
    let appearanceImageCount: Int     // image XObjects in /AP /N /Resources /XObject
}

enum PDFRaw {
    static func annotations(of page: PDFPage) -> [RawAnnotation] {
        []
    }

    static func annotations(inFile url: URL, pageIndex: Int) -> [RawAnnotation] {
        []
    }

    /// The trailer's /Info strings, for tests.
    static func documentInfo(inFile url: URL) -> [String: String] {
        [:]
    }
}
