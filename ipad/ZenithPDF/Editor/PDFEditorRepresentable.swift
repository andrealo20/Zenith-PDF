import SwiftUI

/// Puts the editor controller into SwiftUI. The controller is made by EditorModel and outlives this struct.
struct PDFEditorRepresentable: UIViewControllerRepresentable {
    let controller: EditorViewController

    /// Returns controller.
    func makeUIViewController(context: Context) -> EditorViewController {
        controller
    }

    func updateUIViewController(_ controller: EditorViewController, context: Context) {
    }
}
