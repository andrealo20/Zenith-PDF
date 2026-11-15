import SwiftUI

/// The editor screen: toolbar of section 2.4 around the PDF editor, with the page sidebar, search panel and audio bar.
struct EditorView: View {
    private let document: NoteDocument
    private let launch: EditorLaunchState
    private let onClose: () -> Void
    // Made once in onAppear: SwiftUI builds this struct again on every parent update, and each EditorModel owns
    // a PDF view, a tool picker and the document's callbacks.
    @State private var model: EditorModel?

    init(document: NoteDocument, launch: EditorLaunchState = EditorLaunchState(), onClose: @escaping () -> Void) {
        self.document = document
        self.launch = launch
        self.onClose = onClose
    }

    var body: some View {
        NavigationStack {
            Group {
                if let model {
                    EditorScreen(model: model, onClose: onClose)
                } else {
                    Folio.canvas.ignoresSafeArea()
                }
            }
        }
        .onAppear {
            if model == nil { model = EditorModel(document: document, launch: launch) }
        }
    }
}

private struct EditorScreen: View {
    @Bindable var model: EditorModel
    let onClose: () -> Void

    var body: some View {
        VStack(spacing: 0) {
            if let error = model.document.lastError {
                errorBanner(error)
            }
            if model.mode == .text {
                TextStyleBar(style: $model.textStyle)
                Divider()
            }
            HStack(spacing: 0) {
                if model.showsPages {
                    PageSidebar(document: model.document, currentPage: model.currentPage) { index in
                        model.controller.go(toPage: index, animated: true)
                    }
                    .frame(width: 180)
                    .transition(.move(edge: .leading))
                    Divider()
                }
                PDFEditorRepresentable(controller: model.controller)
                    .ignoresSafeArea(edges: .bottom)
                if model.showsSearch {
                    Divider()
                    NoteSearchPanel(model: model.search)
                        .frame(width: 320)
                        .transition(.move(edge: .trailing))
                }
            }
            AudioBar(audio: model.audio)
        }
        .background(Folio.canvas)
        .animation(.easeInOut(duration: 0.2), value: model.showsPages)
        .animation(.easeInOut(duration: 0.2), value: model.showsSearch)
        .navigationTitle(model.document.title)
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(Folio.raised, for: .navigationBar)
        .toolbar { toolbar }
    }

    @ToolbarContentBuilder private var toolbar: some ToolbarContent {
        ToolbarItem(placement: .topBarLeading) {
            Button {
                model.textBoxes.endEditing()
                model.audio.stopAll()
                onClose()
            } label: {
                Label("Back", systemImage: "chevron.backward")
            }
        }
        ToolbarItem(placement: .principal) {
            Picker("Mode", selection: $model.mode) {
                Text("Write").tag(EditorMode.write)
                Text("Text").tag(EditorMode.text)
                Text("Select").tag(EditorMode.select)
                Text("Read").tag(EditorMode.read)
            }
            .pickerStyle(.segmented)
            .frame(width: 300)
            .disabled(model.document.isReadOnly)
        }
        ToolbarItemGroup(placement: .topBarTrailing) {
            Button { model.undo() } label: { Label("Undo", systemImage: "arrow.uturn.backward") }
                .disabled(!model.canUndo)
            Button { model.redo() } label: { Label("Redo", systemImage: "arrow.uturn.forward") }
                .disabled(!model.canRedo)
            Button { model.addPageAfterCurrent() } label: { Label("Add Page", systemImage: "doc.badge.plus") }
                .disabled(model.document.isReadOnly)
            Button {
                if model.audio.isRecording {
                    model.audio.stopRecording()
                } else {
                    Task { await model.audio.startRecording() }
                }
            } label: {
                if model.audio.isRecording {
                    Label("Stop Recording", systemImage: "stop.circle.fill")
                } else {
                    Label("Record", systemImage: "mic")
                }
            }
            Button { model.showsPages.toggle() } label: { Label("Pages", systemImage: "sidebar.leading") }
            Button { model.showsSearch.toggle() } label: { Label("Search", systemImage: "magnifyingglass") }
            InsertMenu(model: model)
            ShareMenu(document: model.document)
            Menu {
                Toggle("Shape Recognition", isOn: $model.smartShapes)
                Toggle("Draw with Finger", isOn: $model.fingerDrawing)
            } label: {
                Label("More", systemImage: "ellipsis.circle")
            }
        }
    }

    private func errorBanner(_ error: ZenithError) -> some View {
        HStack(spacing: 12) {
            Image(systemName: "exclamationmark.triangle.fill")
                .foregroundStyle(Folio.danger)
            Text(verbatim: error.localizedDescription)
                .font(.callout)
                .foregroundStyle(Folio.text)
            Spacer()
            Button("Dismiss") { model.document.lastError = nil }
        }
        .padding(.horizontal, 16)
        .padding(.vertical, 10)
        .background(Folio.raised)
        .overlay(alignment: .bottom) { Divider() }
    }
}
