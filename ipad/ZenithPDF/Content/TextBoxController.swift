import PDFKit
import UIKit

/// Create and edit text boxes.
@MainActor final class TextBoxController: NSObject, UITextViewDelegate {
    private weak var editor: EditorViewController?
    var style: TextBoxStyle = TextBoxStyle()      // applied to new boxes and to the one being edited
    private(set) var editing: PDFAnnotation?

    init(editor: EditorViewController) {
        self.editor = editor
        super.init()
    }

    /// .text mode: edits the text box under the point, else creates one there (default width 240 pt).
    func handleTap(page: PDFPage, pdfPoint: CGPoint) -> Bool {
        false
    }

    /// Finger double tap in .write: edits the text box under the point; never creates one.
    func editExisting(page: PDFPage, pdfPoint: CGPoint) -> Bool {
        false
    }

    func beginEditing(_ annotation: PDFAnnotation, on page: PDFPage) {
    }

    /// Commits through TextBoxCodec.apply; an empty new box is removed; editor.reclaimFirstResponder().
    func endEditing() {
    }
}
