import SwiftUI

/// SwiftUI screen and toolbar.
struct EditorView: View {
    private let document: NoteDocument
    private let launch: EditorLaunchState
    private let onClose: () -> Void

    init(document: NoteDocument, launch: EditorLaunchState = EditorLaunchState(), onClose: @escaping () -> Void) {
        self.document = document
        self.launch = launch
        self.onClose = onClose
    }

    var body: some View {
        NavigationStack {
            Text(verbatim: document.title)
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .background(Folio.canvas)
                .toolbar {
                    ToolbarItem(placement: .topBarLeading) {
                        Button("Back") { onClose() }
                    }
                }
        }
    }
}
