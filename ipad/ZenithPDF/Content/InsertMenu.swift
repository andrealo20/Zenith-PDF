import SwiftUI

/// Photos, scan, PDF pages, blank page.
struct InsertMenu: View {
    private let model: EditorModel

    init(model: EditorModel) {
        self.model = model
    }

    /// Menu: Photo (PhotosPicker), Photo as Page, Scan Document (if VNDocumentCameraViewController.isSupported),
    /// Pages from PDF (fileImporter .pdf), Blank Page; each calls ImageInsertion or the NoteDocument page operations.
    var body: some View {
        EmptyView()
    }
}
