import SwiftUI

/// Owns LibraryModel; .fullScreenCover(item:) of OpenNote; .onOpenURL;
/// WelcomeSheet while LibraryLocation.shared.needsChoice.
struct RootView: View {
    @State private var model = LibraryModel(location: LibraryLocation.shared)

    var body: some View {
        if LaunchOptions.isUnitTesting {
            Color.clear
        } else {
            LibraryView(model: model, open: { _, _ in })
                .tint(Folio.accent)
                .onAppear {
                    LaunchOptions.markReady(LaunchOptions.demoScene?.rawValue ?? "launch")
                }
        }
    }
}
