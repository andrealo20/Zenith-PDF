import SwiftUI

/// Library search results.
struct SearchResultsView: View {
    private let model: LibraryModel
    private let open: (URL, Bool) -> Void

    init(model: LibraryModel, open: @escaping (URL, Bool) -> Void) {
        self.model = model
        self.open = open
    }

    var body: some View {
        EmptyView()
    }
}
