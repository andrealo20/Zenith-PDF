import SwiftUI

/// Thumbnails sidebar UI.
struct PageSidebar: View {
    let document: NoteDocument
    let currentPage: Int
    let onSelect: (Int) -> Void

    init(document: NoteDocument, currentPage: Int, onSelect: @escaping (Int) -> Void) {
        self.document = document
        self.currentPage = currentPage
        self.onSelect = onSelect
    }

    /// List of thumbnails, .onMove -> reorderPage, context menu: Add Page After, Duplicate, Delete.
    var body: some View {
        EmptyView()
    }
}
