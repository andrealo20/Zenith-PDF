import PhotosUI
import SwiftUI
import UniformTypeIdentifiers
import VisionKit

/// NavigationSplitView, sidebar, toolbar. Presents every LibrarySheet, the welcome sheet included.
struct LibraryView: View {
    @Bindable private var model: LibraryModel
    private let open: (_ note: URL, _ newWindow: Bool) -> Void
    @Environment(\.scenePhase) private var scenePhase
    @State private var namingSubject = false
    @State private var subjectName = ""
    @State private var renamingSubject: FolderItem?
    @State private var newName = ""
    @State private var importingPDF = false
    @State private var choosingPhotos = false
    @State private var photoItems: [PhotosPickerItem] = []
    @State private var createdNote: URL?

    init(model: LibraryModel, open: @escaping (_ note: URL, _ newWindow: Bool) -> Void) {
        _model = Bindable(model)
        self.open = open
    }

    var body: some View {
        pickers(splitView)
            .alert("New Subject", isPresented: $namingSubject) {
                TextField("Subject name", text: $subjectName)
                Button("Cancel", role: .cancel) {}
                Button("Create") { model.createSubject(named: subjectName) }
            }
            .alert("Rename", isPresented: Binding(get: { renamingSubject != nil }, set: { if !$0 { renamingSubject = nil } })) {
                TextField("Subject name", text: $newName)
                Button("Cancel", role: .cancel) { renamingSubject = nil }
                Button("Rename") {
                    if let renamingSubject { model.rename(renamingSubject.url, to: newName) }
                    renamingSubject = nil
                }
            }
            .alert(model.error?.errorDescription ?? "", isPresented: errorShown) {
                Button("OK", role: .cancel) { model.error = nil }
            }
    }

    private var splitView: some View {
        NavigationSplitView {
            sidebar
        } detail: {
            NavigationStack {
                detail
                    .navigationTitle(model.title)
                    .navigationBarTitleDisplayMode(.inline)
                    // The bold serif title sits in the content; the bar keeps only its buttons.
                    .toolbar { ToolbarItem(placement: .principal) { Text(verbatim: "") } }
            }
            .searchable(text: $model.searchText, prompt: "Search notes and handwriting")
        }
        .onAppear { model.refresh() }
        .onChange(of: model.location.root) { model.refresh() }
        .onChange(of: scenePhase) { _, phase in
            // Notes may have changed in Files or on another device while the app was away.
            if phase == .active { model.refresh() }
        }
    }

    /// Sheets, the PDF importer and the photo picker.
    private func pickers<Content: View>(_ content: Content) -> some View {
        content
            .sheet(item: $model.presentedSheet, onDismiss: openCreatedNote) { sheet in
                sheetContent(sheet)
            }
            .fileImporter(isPresented: $importingPDF, allowedContentTypes: [.pdf], allowsMultipleSelection: true) { result in
                if case .success(let urls) = result { _ = model.importPDFs(urls) }
            }
            .photosPicker(isPresented: $choosingPhotos, selection: $photoItems, matching: .images)
            .onChange(of: photoItems) { _, items in
                guard !items.isEmpty else { return }
                Task { await importPhotos(items) }
            }
    }

    /// Errors show here unless a sheet is up; the sheets show their own.
    private var errorShown: Binding<Bool> {
        Binding(get: { model.error != nil && model.presentedSheet == nil }, set: { if !$0 { model.error = nil } })
    }

    // MARK: - Sidebar

    private var sidebar: some View {
        List(selection: Binding<SidebarSelection?>(get: { model.selection }, set: { if let value = $0 { model.selection = value } })) {
            Section {
                Label("All Notes", systemImage: "doc.on.doc").tag(SidebarSelection.all)
                Label("Favourites", systemImage: "star").tag(SidebarSelection.favourites)
                Label("Recently Deleted", systemImage: "trash").tag(SidebarSelection.recentlyDeleted)
            }
            Section("Subjects") {
                ForEach(model.subjects) { subject in
                    Label(subject.name, systemImage: "folder")
                        .tag(SidebarSelection.folder(subject.url))
                        .contextMenu {
                            Button { model.selection = .folder(subject.url) } label: { Label("Open", systemImage: "folder") }
                            Button {
                                newName = subject.name
                                renamingSubject = subject
                            } label: { Label("Rename", systemImage: "pencil") }
                            Button { model.duplicate(subject.url) } label: { Label("Duplicate", systemImage: "plus.square.on.square") }
                            Button { model.presentedSheet = .move(subject.url) } label: { Label("Move", systemImage: "folder.badge.gearshape") }
                            Button(role: .destructive) { model.delete(subject.url) } label: { Label("Delete", systemImage: "trash") }
                        }
                }
                Button {
                    subjectName = ""
                    namingSubject = true
                } label: {
                    Label("New Subject", systemImage: "plus")
                }
                .foregroundStyle(Folio.textMuted)
            }
        }
        .listStyle(.sidebar)
        .scrollContentBackground(.hidden)
        .background(Folio.surface)
        .navigationTitle("Library")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button { model.presentedSheet = .settings } label: { Label("Settings", systemImage: "gearshape") }
                    .accessibilityIdentifier("settingsButton")
            }
        }
    }

    // MARK: - Detail

    @ViewBuilder private var detail: some View {
        if !model.searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            SearchResultsView(model: model, open: open)
        } else if model.selection == .recentlyDeleted {
            TrashView(model: model)
        } else {
            NoteGrid(model: model, open: open)
                .toolbar { gridToolbar }
        }
    }

    @ToolbarContentBuilder private var gridToolbar: some ToolbarContent {
        if let parent = model.parentFolder {
            ToolbarItem(placement: .topBarLeading) {
                Button { model.selection = .folder(parent) } label: {
                    Label(parent.lastPathComponent, systemImage: "chevron.left")
                }
            }
        }
        ToolbarItemGroup(placement: .topBarTrailing) {
            if model.isSelecting {
                Button("Done") { model.isSelecting = false }
            } else {
                newMenu
                sortMenu
                Button("Select") { model.isSelecting = true }
                    .disabled(model.notes.isEmpty && model.folders.isEmpty)
            }
        }
        if model.isSelecting {
            ToolbarItemGroup(placement: .bottomBar) {
                Spacer()
                Button(role: .destructive) { model.deleteSelected() } label: { Label("Delete", systemImage: "trash") }
                    .disabled(model.selectedURLs.isEmpty)
            }
        }
    }

    private var newMenu: some View {
        Menu {
            Button { model.presentedSheet = .newNote } label: { Label("New Note", systemImage: "doc.badge.plus") }
                .accessibilityIdentifier("newNoteButton")
            Button {
                subjectName = ""
                namingSubject = true
            } label: { Label("New Subject", systemImage: "folder.badge.plus") }
            Divider()
            Button { importingPDF = true } label: { Label("Import PDF", systemImage: "doc.richtext") }
            Button { choosingPhotos = true } label: { Label("Import Photos", systemImage: "photo.on.rectangle") }
            if VNDocumentCameraViewController.isSupported {
                Button { model.presentedSheet = .scan } label: { Label("Scan Document", systemImage: "doc.viewfinder") }
            }
        } label: {
            Label("New", systemImage: "plus")
        }
        .accessibilityIdentifier("newMenu")
    }

    private var sortMenu: some View {
        Menu {
            Picker("Sort", selection: $model.sort) {
                Text("Date Modified").tag(LibrarySort.modified)
                Text("Date Created").tag(LibrarySort.created)
                Text("Name").tag(LibrarySort.name)
            }
        } label: {
            Label("Sort", systemImage: "arrow.up.arrow.down")
        }
    }

    // MARK: - Sheets

    @ViewBuilder private func sheetContent(_ sheet: LibrarySheet) -> some View {
        switch sheet {
        case .newNote:
            NewNoteSheet(model: model) { url in
                // Opened once the sheet is gone: a full-screen cover cannot appear while a sheet is dismissing.
                createdNote = url
                model.presentedSheet = nil
            }
        case .settings:
            SettingsView(model: model)
        case .move(let url):
            MoveSheet(model: model, item: url)
        case .scan:
            DocumentScanner { images in
                model.presentedSheet = nil
                if !images.isEmpty { _ = model.createNote(fromImages: images) }
            }
            .ignoresSafeArea()
        case .welcome:
            WelcomeSheet(model: model)
                .interactiveDismissDisabled()
        }
    }

    private func openCreatedNote() {
        guard let url = createdNote else { return }
        createdNote = nil
        open(url, false)
    }

    private func importPhotos(_ items: [PhotosPickerItem]) async {
        var images: [UIImage] = []
        for item in items {
            if let data = try? await item.loadTransferable(type: Data.self), let image = UIImage(data: data) {
                images.append(image)
            }
        }
        photoItems = []
        if !images.isEmpty { _ = model.createNote(fromImages: images) }
    }
}
