import PDFKit

enum ZenithKey {
    // For PDFAnnotation.setValue / value(forAnnotationKey:) / removeValue(forAnnotationKey:): raw values carry the slash.
    static let widths          = PDFAnnotationKey(rawValue: "/ZenithWidths")
    static let highlighter     = PDFAnnotationKey(rawValue: "/ZenithHighlighter")
    static let pencilKit       = PDFAnnotationKey(rawValue: "/ZenithPencilKit")
    static let inkCheck        = PDFAnnotationKey(rawValue: "/ZenithInkCheck")
    static let imageData       = PDFAnnotationKey(rawValue: "/ZenithImageData")
    static let opacity         = PDFAnnotationKey(rawValue: "/CA")
    static let identifier      = PDFAnnotationKey(rawValue: "/NM")
    static let defaultStyle    = PDFAnnotationKey(rawValue: "/DS")
    static let rectDifferences = PDFAnnotationKey(rawValue: "/RD")
    /// Document information dictionary entry, through PDFDocument.documentAttributes.
    static let noteID = PDFDocumentAttribute(rawValue: "ZenithNoteID")

    // For CGPDFDictionary lookups: no slash.
    enum Raw {
        static let widths = "ZenithWidths", highlighter = "ZenithHighlighter", pencilKit = "ZenithPencilKit"
        static let inkCheck = "ZenithInkCheck", imageData = "ZenithImageData", inkList = "InkList", identifier = "NM"
        static let defaultStyle = "DS", rectDifferences = "RD", noteID = "ZenithNoteID"
    }
}
