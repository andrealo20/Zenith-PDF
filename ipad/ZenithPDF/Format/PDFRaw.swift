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

    /// /F bit 2.
    var isHidden: Bool { flags & 2 != 0 }

    /// /C as RGB the way the desktop reads it: one number is grey, four are CMYK.
    var rgb: RGB? {
        guard let c = color else { return nil }
        switch c.count {
        case 1: return RGB(r: c[0], g: c[0], b: c[0])
        case 3: return RGB(r: c[0], g: c[1], b: c[2])
        case 4: return RGB(r: (1 - c[0]) * (1 - c[3]), g: (1 - c[1]) * (1 - c[3]), b: (1 - c[2]) * (1 - c[3]))
        default: return nil
        }
    }
}

enum PDFRaw {
    static func annotations(of page: PDFPage) -> [RawAnnotation] {
        guard let dictionary = page.pageRef?.dictionary else { return [] }
        return annotations(inPage: dictionary)
    }

    static func annotations(inFile url: URL, pageIndex: Int) -> [RawAnnotation] {
        // CGPDFDocument pages are numbered from 1.
        guard let document = CGPDFDocument(url as CFURL), pageIndex >= 0, pageIndex < document.numberOfPages,
              let page = document.page(at: pageIndex + 1), let dictionary = page.dictionary else { return [] }
        return annotations(inPage: dictionary)
    }

    /// The trailer's /Info strings, for tests.
    static func documentInfo(inFile url: URL) -> [String: String] {
        guard let document = CGPDFDocument(url as CFURL), let info = document.info else { return [:] }
        var result: [String: String] = [:]
        CGPDFDictionaryApplyBlock(info, { key, object, _ in
            var string: CGPDFStringRef? = nil
            if CGPDFObjectGetValue(object, .string, &string), let string,
               let text = CGPDFStringCopyTextString(string) as String? {
                result[String(cString: key)] = text
            }
            return true
        }, nil)
        return result
    }

    // MARK: Reading

    private static func annotations(inPage page: CGPDFDictionaryRef) -> [RawAnnotation] {
        var annots: CGPDFArrayRef? = nil
        guard CGPDFDictionaryGetArray(page, "Annots", &annots), let annots else { return [] }
        var result: [RawAnnotation] = []
        for index in 0..<CGPDFArrayGetCount(annots) {
            var dictionary: CGPDFDictionaryRef? = nil
            guard CGPDFArrayGetDictionary(annots, index, &dictionary), let dictionary else { continue }
            result.append(annotation(dictionary))
        }
        return result
    }

    private static func annotation(_ d: CGPDFDictionaryRef) -> RawAnnotation {
        let rectValues = numbers(array(d, "Rect")) ?? []
        var rect = CGRect.zero
        if rectValues.count == 4 {
            rect = CGRect(x: rectValues[0], y: rectValues[1],
                          width: rectValues[2] - rectValues[0], height: rectValues[3] - rectValues[1]).standardized
        }
        var inkList: [[CGPoint]]? = nil
        if let list = array(d, ZenithKey.Raw.inkList) {
            var paths: [[CGPoint]] = []
            for index in 0..<CGPDFArrayGetCount(list) {
                var path: CGPDFArrayRef? = nil
                guard CGPDFArrayGetArray(list, index, &path), let values = numbers(path) else { continue }
                paths.append(stride(from: 0, to: values.count - 1, by: 2).map { CGPoint(x: values[$0], y: values[$0 + 1]) })
            }
            inkList = paths
        }
        var widths: [[CGFloat]]? = nil
        if let list = array(d, ZenithKey.Raw.widths) {
            var all: [[CGFloat]] = []
            for index in 0..<CGPDFArrayGetCount(list) {
                var element: CGPDFArrayRef? = nil
                guard CGPDFArrayGetArray(list, index, &element), let values = numbers(element) else {
                    all = []
                    break
                }
                all.append(values)
            }
            if all.count == CGPDFArrayGetCount(list) && !all.isEmpty { widths = all }
        }
        var appearanceData: Data? = nil
        var imageCount = 0
        var appearance: CGPDFDictionaryRef? = nil
        var normal: CGPDFStreamRef? = nil
        if CGPDFDictionaryGetDictionary(d, "AP", &appearance), let appearance,
           CGPDFDictionaryGetStream(appearance, "N", &normal), let normal {
            var format = CGPDFDataFormat.raw
            appearanceData = CGPDFStreamCopyData(normal, &format) as Data?
            imageCount = images(in: normal)
        }
        return RawAnnotation(
            subtype: name(d, "Subtype") ?? "",
            identifier: string(d, ZenithKey.Raw.identifier),
            rect: rect,
            flags: Int(number(d, "F") ?? 0),
            color: numbers(array(d, "C")),
            opacity: number(d, "CA"),
            borderWidth: borderWidth(d),
            inkList: inkList,
            widths: widths,
            highlighter: flag(d, ZenithKey.Raw.highlighter),
            pencilKit: string(d, ZenithKey.Raw.pencilKit),
            inkCheck: string(d, ZenithKey.Raw.inkCheck),
            imageData: string(d, ZenithKey.Raw.imageData),
            contents: string(d, "Contents"),
            defaultAppearance: string(d, "DA"),
            defaultStyle: string(d, ZenithKey.Raw.defaultStyle),
            rectDifferences: numbers(array(d, ZenithKey.Raw.rectDifferences)),
            appearanceData: appearanceData,
            appearanceImageCount: imageCount
        )
    }

    private static func images(in stream: CGPDFStreamRef) -> Int {
        guard let form = CGPDFStreamGetDictionary(stream) else { return 0 }
        var resources: CGPDFDictionaryRef? = nil
        var xobjects: CGPDFDictionaryRef? = nil
        guard CGPDFDictionaryGetDictionary(form, "Resources", &resources), let resources,
              CGPDFDictionaryGetDictionary(resources, "XObject", &xobjects), let xobjects else { return 0 }
        var count = 0
        CGPDFDictionaryApplyBlock(xobjects, { _, object, _ in
            var image: CGPDFStreamRef? = nil
            if CGPDFObjectGetValue(object, .stream, &image), let image, let info = CGPDFStreamGetDictionary(image),
               name(info, "Subtype") == "Image" {
                count += 1
            }
            return true
        }, nil)
        return count
    }

    private static func borderWidth(_ d: CGPDFDictionaryRef) -> CGFloat? {
        var style: CGPDFDictionaryRef? = nil
        if CGPDFDictionaryGetDictionary(d, "BS", &style), let style, let width = number(style, "W") {
            return width
        }
        if let border = numbers(array(d, "Border")), border.count >= 3 { return border[2] }
        return nil
    }

    private static func array(_ d: CGPDFDictionaryRef, _ key: String) -> CGPDFArrayRef? {
        var value: CGPDFArrayRef? = nil
        guard CGPDFDictionaryGetArray(d, key, &value) else { return nil }
        return value
    }

    /// Every element as a number, or nil when one is not a number.
    private static func numbers(_ array: CGPDFArrayRef?) -> [CGFloat]? {
        guard let array else { return nil }
        var result: [CGFloat] = []
        for index in 0..<CGPDFArrayGetCount(array) {
            var value: CGPDFReal = 0
            guard CGPDFArrayGetNumber(array, index, &value) else { return nil }
            result.append(CGFloat(value))
        }
        return result
    }

    private static func number(_ d: CGPDFDictionaryRef, _ key: String) -> CGFloat? {
        var value: CGPDFReal = 0
        return CGPDFDictionaryGetNumber(d, key, &value) ? CGFloat(value) : nil
    }

    private static func flag(_ d: CGPDFDictionaryRef, _ key: String) -> Bool {
        var value: CGPDFBoolean = 0
        if CGPDFDictionaryGetBoolean(d, key, &value) { return value != 0 }
        return (number(d, key) ?? 0) != 0
    }

    private static func name(_ d: CGPDFDictionaryRef, _ key: String) -> String? {
        var value: UnsafePointer<CChar>? = nil
        guard CGPDFDictionaryGetName(d, key, &value), let value else { return nil }
        return String(cString: value)
    }

    private static func string(_ d: CGPDFDictionaryRef, _ key: String) -> String? {
        var value: CGPDFStringRef? = nil
        guard CGPDFDictionaryGetString(d, key, &value), let value else { return nil }
        return CGPDFStringCopyTextString(value) as String?
    }
}
