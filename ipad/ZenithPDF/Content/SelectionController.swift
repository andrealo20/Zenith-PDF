import PDFKit
import UIKit

/// Select, move, resize, delete, duplicate.
@MainActor final class SelectionController {
    private weak var editor: EditorViewController?
    private let textBoxes: TextBoxController
    private(set) var selected: PDFAnnotation?

    init(editor: EditorViewController, textBoxes: TextBoxController) {
        self.editor = editor
        self.textBoxes = textBoxes
    }

    /// Text boxes and ImageAnnotations only.
    func handleTap(page: PDFPage, pdfPoint: CGPoint) -> Bool {
        false
    }

    func clear() {
    }

    func deleteSelected() {
    }

    func duplicateSelected() {
    }
}
