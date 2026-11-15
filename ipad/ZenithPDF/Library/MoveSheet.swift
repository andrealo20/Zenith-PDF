import SwiftUI

/// Folder chooser for move.
struct MoveSheet: View {
    private let model: LibraryModel
    private let item: URL

    init(model: LibraryModel, item: URL) {
        self.model = model
        self.item = item
    }

    var body: some View {
        EmptyView()
    }
}
