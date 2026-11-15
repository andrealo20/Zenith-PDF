import SwiftUI

/// First launch: pick a folder or keep notes in the app.
struct WelcomeSheet: View {
    private let model: LibraryModel

    init(model: LibraryModel) {
        self.model = model
    }

    /// Choose Folder, or Keep in the App.
    var body: some View {
        EmptyView()
    }
}
