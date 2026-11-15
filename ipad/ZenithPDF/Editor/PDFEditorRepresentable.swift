import SwiftUI

/// UIViewControllerRepresentable around the editor controller.
struct PDFEditorRepresentable: UIViewControllerRepresentable {
    let controller: EditorViewController

    /// Returns controller.
    func makeUIViewController(context: Context) -> EditorViewController {
        controller
    }

    func updateUIViewController(_ controller: EditorViewController, context: Context) {
    }
}
