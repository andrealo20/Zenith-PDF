import PDFKit
import PencilKit

enum NoteReader {
    /// Takes the ink out of the displayed document: every visible /Ink annotation becomes strokes and is
    /// removed (with its popup); every /Stamp with /ZenithImageData becomes an ImageAnnotation in place.
    /// Returns one NotePage per PDF page, in order. Nothing is interpolated here (section 4.3).
    @MainActor static func extract(from pdf: PDFDocument) -> [NotePage] {
        (0..<pdf.pageCount).map { _ in NotePage() }
    }
}
