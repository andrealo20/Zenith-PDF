import SwiftUI

/// Template chooser.
struct NewNoteSheet: View {
    private let model: LibraryModel
    private let onCreated: (URL) -> Void

    init(model: LibraryModel, onCreated: @escaping (URL) -> Void) {
        self.model = model
        self.onCreated = onCreated
    }

    var body: some View {
        EmptyView()
    }
}
