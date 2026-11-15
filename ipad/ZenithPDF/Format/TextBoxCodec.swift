import PDFKit
import UIKit

enum TextBoxCodec {
    static func makeAnnotation(text: String, style: TextBoxStyle, bounds: CGRect) -> PDFAnnotation {
        PDFAnnotation(bounds: bounds, forType: .freeText, withProperties: nil)
    }

    /// Sets the PDFKit properties, then writes /DS in the desktop's format and removes /RD (section 4.4).
    static func apply(text: String, style: TextBoxStyle, to annotation: PDFAnnotation) {
    }

    /// /DS first when present, as the desktop reads it.
    static func style(of annotation: PDFAnnotation) -> TextBoxStyle {
        TextBoxStyle()
    }

    /// type == "FreeText"
    static func isTextBox(_ annotation: PDFAnnotation) -> Bool {
        annotation.type == "FreeText"
    }

    /// The desktop's /DS string (port of pdf_annots._style_string): "font: Times,serif 14pt; text-align:left;
    /// color:#000000", then "font-weight:bold" and "font-style:italic" when set, joined with "; ".
    static func styleString(_ style: TextBoxStyle) -> String {
        ""
    }

    /// Box for text laid out at `width` from a top-left corner in PDF space (y grows upward in PDF space).
    static func fittedBounds(text: String, style: TextBoxStyle, topLeft: CGPoint, width: CGFloat) -> CGRect {
        CGRect(x: topLeft.x, y: topLeft.y, width: width, height: 0)
    }
}
