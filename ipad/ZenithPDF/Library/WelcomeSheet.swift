import SwiftUI
import UniformTypeIdentifiers

/// First launch: pick a folder or keep notes in the app. It cannot be swiped away; one of the two buttons ends it.
struct WelcomeSheet: View {
    private let model: LibraryModel
    @State private var choosingFolder = false

    init(model: LibraryModel) {
        self.model = model
    }

    /// Choose Folder, or Keep in the App.
    var body: some View {
        VStack(spacing: 22) {
            Image(systemName: "folder.badge.questionmark")
                .font(.system(size: 52))
                .foregroundStyle(Folio.textMuted)
            Text("Where should your notes live?")
                .folioTitle()
                .multilineTextAlignment(.center)
            Text("Pick a folder in iCloud Drive or On My iPad, so your notes stay when the app is deleted.")
                .foregroundStyle(Folio.textMuted)
                .multilineTextAlignment(.center)
            if let error = model.error {
                Text(error.errorDescription ?? "")
                    .foregroundStyle(Folio.danger)
                    .multilineTextAlignment(.center)
            }
            VStack(spacing: 12) {
                Button {
                    model.error = nil
                    choosingFolder = true
                } label: {
                    Text("Choose a Folder").frame(maxWidth: .infinity)
                }
                .buttonStyle(.borderedProminent)
                .accessibilityIdentifier("chooseFolderButton")
                Button {
                    model.error = nil
                    model.keepNotesInApp()
                } label: {
                    Text("Keep in the App").frame(maxWidth: .infinity)
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("keepInAppButton")
            }
            .controlSize(.large)
            .frame(maxWidth: 360)
            Text("Notes kept inside the app are deleted together with the app. To renew the app, sign it again over the old one instead of deleting it.")
                .font(.footnote)
                .foregroundStyle(Folio.textMuted)
                .multilineTextAlignment(.center)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Folio.window)
        .fileImporter(isPresented: $choosingFolder, allowedContentTypes: [.folder]) { result in
            if case .success(let url) = result { model.chooseLibraryFolder(url) }
        }
    }
}
