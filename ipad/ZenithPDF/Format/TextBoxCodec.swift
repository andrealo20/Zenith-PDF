import PDFKit
import UIKit

enum TextBoxCodec {
    /// The desktop's TEXT_PADDING: points between a text box's edge and its text.
    private static let padding: CGFloat = 4

    static func makeAnnotation(text: String, style: TextBoxStyle, bounds: CGRect) -> PDFAnnotation {
        let annotation = PDFAnnotation(bounds: bounds, forType: .freeText, withProperties: nil)
        apply(text: text, style: style, to: annotation)
        return annotation
    }

    /// Sets the PDFKit properties, then writes /DS in the desktop's format and removes /RD (section 4.4).
    static func apply(text: String, style: TextBoxStyle, to annotation: PDFAnnotation) {
        annotation.contents = text
        annotation.font = style.uiFont
        annotation.fontColor = style.color.uiColor
        annotation.alignment = NSTextAlignment(rawValue: style.alignment) ?? .left
        let border = PDFBorder()
        border.lineWidth = 0
        annotation.border = border
        annotation.color = .clear
        // An empty /C: the desktop fills a box with the colour /C holds.
        _ = annotation.setValue(NSArray(), forAnnotationKey: .color)
        _ = annotation.setValue(styleString(style) as NSString, forAnnotationKey: ZenithKey.defaultStyle)
        annotation.removeValue(forAnnotationKey: ZenithKey.rectDifferences)
    }

    /// /DS first when present, as the desktop reads it.
    static func style(of annotation: PDFAnnotation) -> TextBoxStyle {
        var style = TextBoxStyle()
        let fontName = annotation.font?.fontName ?? ""
        let lower = fontName.lowercased()
        var font = family(fontName) ?? .helvetica
        var bold = lower.contains("bold")
        var italic = lower.contains("italic") || lower.contains("oblique")
        if let css = annotation.value(forAnnotationKey: ZenithKey.defaultStyle) as? String {
            if let named = cssFamily(css), let chosen = family(named) { font = chosen }
            let compact = css.lowercased().replacingOccurrences(of: " ", with: "")
            bold = bold || compact.contains("font-weight:bold")
                || compact.range(of: "font-weight:[6-9]00", options: .regularExpression) != nil
            italic = italic || compact.contains("font-style:italic") || compact.contains("font-style:oblique")
        }
        style.font = font
        style.bold = bold
        style.italic = italic
        if let size = annotation.font?.pointSize, size > 0 { style.size = size }
        if let color = annotation.fontColor { style.color = RGB(color) }
        let alignment = annotation.alignment.rawValue
        style.alignment = (0...2).contains(alignment) ? alignment : 0
        return style
    }

    /// type == "FreeText"
    static func isTextBox(_ annotation: PDFAnnotation) -> Bool {
        annotation.type == "FreeText"
    }

    /// The desktop's /DS string (port of pdf_annots._style_string): "font: Times,serif 14pt; text-align:left;
    /// color:#000000", then "font-weight:bold" and "font-style:italic" when set, joined with "; ".
    static func styleString(_ style: TextBoxStyle) -> String {
        let family: String
        switch style.font {
        case .helvetica: family = "Helvetica,sans-serif"
        case .times: family = "Times,serif"
        case .courier: family = "Courier,monospace"
        }
        let aligns = ["left", "center", "right"]
        let align = aligns.indices.contains(style.alignment) ? aligns[style.alignment] : "left"
        // Python's round() rounds halves to even.
        let hex = [style.color.r, style.color.g, style.color.b]
            .map { String(format: "%02lX", Int((min(max($0, 0), 1) * 255).rounded(.toNearestOrEven))) }
            .joined()
        var parts = ["font: \(family) \(number(style.size))pt", "text-align:\(align)", "color:#\(hex)"]
        if style.bold { parts.append("font-weight:bold") }
        if style.italic { parts.append("font-style:italic") }
        return parts.joined(separator: "; ")
    }

    /// Box for text laid out at `width` from a top-left corner in PDF space (y grows upward in PDF space).
    static func fittedBounds(text: String, style: TextBoxStyle, topLeft: CGPoint, width: CGFloat) -> CGRect {
        let inner = max(width - 2 * padding, 1)
        let measured = (text.isEmpty ? " " : text) as NSString
        let paragraph = NSMutableParagraphStyle()
        paragraph.alignment = NSTextAlignment(rawValue: style.alignment) ?? .left
        let size = measured.boundingRect(with: CGSize(width: inner, height: .greatestFiniteMagnitude),
                                         options: [.usesLineFragmentOrigin, .usesFontLeading],
                                         attributes: [.font: style.uiFont, .paragraphStyle: paragraph], context: nil)
        let height = size.height.rounded(.up) + 2 * padding
        return CGRect(x: topLeft.x, y: topLeft.y - height, width: width, height: height)
    }

    /// appearance.num: up to 3 decimals, trailing zeros dropped.
    private static func number(_ value: CGFloat) -> String {
        var text = String(format: "%.3f", Double(value))
        while text.contains(".") && (text.hasSuffix("0") || text.hasSuffix(".")) { text.removeLast() }
        return text == "-0" || text.isEmpty ? "0" : text
    }

    /// pdf_annots._family: the built-in font a name or CSS family stands for.
    private static func family(_ text: String) -> TextFont? {
        let lower = text.lowercased()
        let serif = lower.range(of: "(?<!sans-)serif", options: .regularExpression) != nil
        if lower.contains("times") || lower.contains("georgia") || serif { return .times }
        if lower.contains("cour") || lower.contains("mono") || lower.contains("consol") { return .courier }
        if lower.contains("helv") || lower.contains("arial") || lower.contains("sans") { return .helvetica }
        return nil
    }

    /// The value of "font:" or "font-family:" in a style string.
    private static func cssFamily(_ css: String) -> String? {
        guard let regex = try? NSRegularExpression(pattern: "font(?:-family)?\\s*:\\s*([^;]+)", options: [.caseInsensitive]),
              let match = regex.firstMatch(in: css, range: NSRange(css.startIndex..., in: css)),
              let range = Range(match.range(at: 1), in: css) else { return nil }
        return String(css[range])
    }
}
