import SwiftUI
import UniformTypeIdentifiers

/// Library location, defaults, about.
struct SettingsView: View {
    @Bindable private var model: LibraryModel
    @Environment(\.dismiss) private var dismiss
    @AppStorage(SettingKey.fingerDrawing) private var fingerDrawing = false
    @AppStorage(SettingKey.smartShapes) private var smartShapes = false
    @State private var choosingFolder = false

    private static let repository = URL(string: "https://github.com/andrealo20/Zenith-PDF")

    init(model: LibraryModel) {
        _model = Bindable(model)
    }

    var body: some View {
        NavigationStack {
            Form {
                locationSection
                Section("Paper for new notes") {
                    Picker("Paper", selection: $model.newNoteTemplate.pattern) {
                        ForEach(PaperPattern.allCases, id: \.self) { pattern in Text(NewNoteSheet.label(pattern)).tag(pattern) }
                    }
                    Picker("Colour", selection: $model.newNoteTemplate.color) {
                        ForEach(PaperColor.allCases, id: \.self) { color in Text(NewNoteSheet.label(color)).tag(color) }
                    }
                    Picker("Size", selection: $model.newNoteTemplate.size) {
                        ForEach(PaperSize.allCases, id: \.self) { size in Text(NewNoteSheet.label(size)).tag(size) }
                    }
                }
                Section {
                    Toggle("Draw with Finger", isOn: $fingerDrawing)
                    Toggle("Shape Recognition", isOn: $smartShapes)
                }
                Section {
                    LabeledContent("Zenith PDF", value: tr("Version %@", SettingsView.version as NSString))
                    LabeledContent("Licence", value: "GPL-3.0-or-later")
                    if let repository = SettingsView.repository {
                        Link("Source code", destination: repository)
                    }
                }
            }
            .scrollContentBackground(.hidden)
            .background(Folio.window)
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .fileImporter(isPresented: $choosingFolder, allowedContentTypes: [.folder]) { result in
            if case .success(let url) = result { model.chooseLibraryFolder(url) }
        }
        .onAppear { model.markReady(.settings) }
    }

    private var locationSection: some View {
        Section("Notes location") {
            Label(locationName, systemImage: model.isLibraryInsideApp ? "ipad" : "folder")
            if let problem = model.location.problem {
                Label(problem, systemImage: "exclamationmark.triangle")
                    .foregroundStyle(Folio.danger)
            }
            if model.isLibraryInsideApp {
                VStack(alignment: .leading, spacing: 4) {
                    Label("Notes are kept inside the app.", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(Folio.danger)
                    Text("Notes kept inside the app are deleted together with the app. To renew the app, sign it again over the old one instead of deleting it.")
                        .font(.footnote)
                        .foregroundStyle(Folio.textMuted)
                }
                .accessibilityIdentifier("localLibraryWarning")
            }
            if let error = model.error {
                Text(error.errorDescription ?? "")
                    .foregroundStyle(Folio.danger)
            }
            Button("Choose Folder") {
                model.error = nil
                choosingFolder = true
            }
            if !model.isLibraryInsideApp {
                Button("On My iPad") { model.keepNotesInApp() }
            }
        }
    }

    private var locationName: String {
        switch model.location.kind {
        case .local: return tr("On My iPad")
        case .folder(let name): return name
        }
    }

    private static var version: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? ""
    }
}
