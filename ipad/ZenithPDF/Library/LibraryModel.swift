import Foundation
import Observation
import PDFKit
import UIKit

enum SidebarSelection: Hashable { case all, favourites, recentlyDeleted, folder(URL) }

enum LibrarySheet: Identifiable, Hashable {
    case newNote, settings, move(URL), scan, welcome

    var id: String {
        switch self {
        case .newNote: return "newNote"
        case .settings: return "settings"
        case .move(let url): return "move:" + url.path
        case .scan: return "scan"
        case .welcome: return "welcome"
        }
    }
}

/// Library state and actions. Every action refreshes the lists and reports a failure through `error`.
@MainActor @Observable final class LibraryModel {
    let location: LibraryLocation
    private let defaults: UserDefaults
    private let searchDebouncer: Debouncer
    @ObservationIgnored private var searchTask: Task<Void, Never>?
    @ObservationIgnored private static var readyMarked = false

    /// Tests pass their own defaults so a run never changes the app's saved sort or paper.
    init(location: LibraryLocation, defaults: UserDefaults = .standard) {
        self.location = location
        self.defaults = defaults
        searchDebouncer = Debouncer(seconds: 0.3)
        sort = defaults.string(forKey: SettingKey.sort).flatMap { LibrarySort(rawValue: $0) } ?? .modified
        newNoteTemplate = defaults.data(forKey: SettingKey.newNoteTemplate)
            .flatMap { try? JSONDecoder().decode(PaperTemplate.self, from: $0) } ?? .standard
        presentedSheet = location.needsChoice ? .welcome : nil
    }

    var store: LibraryStore { LibraryStore(root: location.root) }
    var trashStore: TrashStore { TrashStore(library: store) }

    var selection: SidebarSelection = .all {      // refreshes
        didSet {
            isSelecting = false
            refresh()
        }
    }

    var sort: LibrarySort = .modified {           // SettingKey.sort
        didSet {
            defaults.set(sort.rawValue, forKey: SettingKey.sort)
            notes = LibraryStore.sorted(notes, by: sort)
        }
    }

    /// The paper the New Note sheet starts from: the last one used, also set in Settings. SettingKey.newNoteTemplate.
    var newNoteTemplate: PaperTemplate = .standard {
        didSet {
            if let data = try? JSONEncoder().encode(newNoteTemplate) {
                defaults.set(data, forKey: SettingKey.newNoteTemplate)
            }
        }
    }

    var presentedSheet: LibrarySheet? = nil

    var searchText: String = "" {                 // runs the search, 0.3 s debounce
        didSet {
            guard searchText != oldValue else { return }
            searchTask?.cancel()
            if searchText.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                searchDebouncer.cancel()
                searchResults = []
                isSearching = false
                return
            }
            isSearching = true
            searchDebouncer.schedule { [weak self] in self?.runSearch() }
        }
    }

    private(set) var subjects: [FolderItem] = []  // top-level folders, for the sidebar
    private(set) var folders: [FolderItem] = []   // inside the selected folder
    private(set) var notes: [NoteItem] = []
    private(set) var trash: [TrashedItem] = []
    private(set) var searchResults: [LibrarySearchResult] = []
    /// true from a change of searchText until its results are in.
    private(set) var isSearching = false
    var error: ZenithError? = nil

    /// Select mode of the grid: taps pick notes and folders instead of opening them.
    var isSelecting = false {
        didSet { if !isSelecting { selectedURLs = [] } }
    }
    var selectedURLs: Set<URL> = []

    /// nil unless selection is .folder
    var currentFolder: URL? {
        if case .folder(let url) = selection { return url }
        return nil
    }

    var title: String {
        switch selection {
        case .all: return tr("All Notes")
        case .favourites: return tr("Favourites")
        case .recentlyDeleted: return tr("Recently Deleted")
        case .folder(let url): return url.lastPathComponent
        }
    }

    /// The folder above a nested selected folder; nil for a subject (its parent is the root) and for the other rows.
    var parentFolder: URL? {
        guard let folder = currentFolder else { return nil }
        let parent = folder.deletingLastPathComponent()
        return SidecarLayout.isInside(location.root, root: parent) ? nil : parent.standardizedFileURL.resolvingSymlinksInPath()
    }

    /// The warning row of Settings: notes live in the app's own folder and go when the app is deleted.
    var isLibraryInsideApp: Bool { location.kind == .local }

    func refresh() {
        // The shell may switch to a demo root after building the model, so the sheet also goes away here.
        if location.needsChoice, presentedSheet == nil { presentedSheet = .welcome }
        if !location.needsChoice, presentedSheet == .welcome { presentedSheet = nil }
        let store = self.store
        do {
            subjects = try store.folders(in: nil)
        } catch {
            subjects = []
            report(error, about: store.root)
        }
        if let folder = currentFolder, !FileStore.exists(folder) {
            selection = .all                      // didSet refreshes again
            return
        }
        do {
            switch selection {
            case .all:
                folders = []
                notes = LibraryStore.sorted(try store.notes(in: nil, recursive: true), by: sort)
            case .favourites:
                folders = []
                notes = LibraryStore.sorted(try store.favourites(), by: sort)
            case .recentlyDeleted:
                folders = []
                notes = []
            case .folder(let url):
                folders = try store.folders(in: url)
                notes = LibraryStore.sorted(try store.notes(in: url, recursive: false), by: sort)
            }
        } catch {
            folders = []
            notes = []
            report(error, about: currentFolder ?? store.root)
        }
        let trashStore = self.trashStore
        do {
            try trashStore.purge()
            trash = try trashStore.items()
        } catch {
            trash = []
            log.error("Recently Deleted not listed: \(error.localizedDescription, privacy: .public)")
        }
        let visible = Set(folders.map(\.url) + notes.map(\.url))
        selectedURLs = selectedURLs.intersection(visible)
    }

    /// A folder in the selected folder; at the root for every other row.
    func createSubject(named name: String) {
        let store = self.store, parent = currentFolder
        _ = attempt(about: parent ?? store.root) { try store.createFolder(named: name, in: parent) }
        refresh()
    }

    /// In currentFolder.
    func createNote(template: PaperTemplate, name: String?) -> URL? {
        let store = self.store, folder = currentFolder
        let trimmed = name?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let url = attempt(about: folder ?? store.root) {
            try store.createNote(named: trimmed.isEmpty ? tr("Untitled") : trimmed,
                                 pdfData: TemplateRenderer.pdfData(for: template), template: template, in: folder)
        }
        refresh()
        return url
    }

    /// Copies each PDF into currentFolder, inside its security-scoped access. Files that are not PDFs are skipped.
    func importPDFs(_ urls: [URL]) -> [URL] {
        let store = self.store, folder = currentFolder
        let imported = urls.compactMap { url -> URL? in
            let scoped = url.startAccessingSecurityScopedResource()
            defer { if scoped { url.stopAccessingSecurityScopedResource() } }
            guard PDFDocument(url: url) != nil else {
                error = .invalidPDF
                return nil
            }
            return attempt(about: url) { try store.importPDF(from: url, into: folder) }
        }
        refresh()
        return imported
    }

    func createNote(fromImages images: [UIImage]) -> URL? {
        let store = self.store, folder = currentFolder
        guard let data = PageFactory.pdfData(from: images) else {
            error = .invalidPDF
            return nil
        }
        let url = attempt(about: folder ?? store.root) {
            try store.createNote(named: tr("Untitled"), pdfData: data, template: nil, in: folder)
        }
        refresh()
        return url
    }

    func rename(_ url: URL, to name: String) {
        let store = self.store
        guard let renamed = attempt(about: url, { try store.rename(url, to: name) }) else { return refresh() }
        followSelection(from: url, to: renamed)
        refresh()
    }

    func duplicate(_ url: URL) {
        let store = self.store
        _ = attempt(about: url) { try store.duplicate(url) }
        refresh()
    }

    func move(_ url: URL, to folder: URL?) {
        let store = self.store
        guard let moved = attempt(about: url, { try store.move(url, to: folder) }) else { return refresh() }
        followSelection(from: url, to: moved)
        refresh()
    }

    func toggleFavourite(_ note: NoteItem) {
        let store = self.store
        _ = attempt(about: note.url) { try store.setFavourite(!note.isFavourite, for: note.url) }
        refresh()
    }

    func delete(_ url: URL) {
        let trashStore = self.trashStore
        if attempt(about: url, { try trashStore.trash(url) }) != nil {
            followSelection(from: url, to: nil)
        }
        refresh()
    }

    /// Select mode: everything picked goes to Recently Deleted.
    func deleteSelected() {
        let trashStore = self.trashStore
        for url in selectedURLs {
            if attempt(about: url, { try trashStore.trash(url) }) != nil {
                followSelection(from: url, to: nil)
            }
        }
        isSelecting = false
        refresh()
    }

    func restore(_ item: TrashedItem) {
        let trashStore = self.trashStore
        _ = attempt(about: store.url(forRelativePath: item.originalPath)) { try trashStore.restore(item) }
        refresh()
    }

    func deletePermanently(_ item: TrashedItem) {
        let trashStore = self.trashStore
        _ = attempt(about: store.url(forRelativePath: item.originalPath)) { try trashStore.deletePermanently(item) }
        refresh()
    }

    func emptyTrash() {
        let trashStore = self.trashStore
        _ = attempt(about: store.root) { try trashStore.empty() }
        refresh()
    }

    /// Welcome and Settings: the app's own folder. Notes already in another folder stay there.
    func keepNotesInApp() {
        location.useLocal()
        finishLocationChange()
    }

    /// Welcome and Settings: a folder from the folder picker. false when it cannot be opened (error is set).
    @discardableResult func chooseLibraryFolder(_ url: URL) -> Bool {
        do {
            try location.choose(folder: url)
        } catch {
            report(error, about: url)
            return false
        }
        finishLocationChange()
        return true
    }

    /// Writes the demo ready marker when `scene` is the scene of this launch, once per launch.
    func markReady(_ scene: DemoScene) {
        guard LaunchOptions.demoScene == scene, !LibraryModel.readyMarked else { return }
        LibraryModel.readyMarked = true
        LaunchOptions.markReady(scene.rawValue)
    }

    // MARK: - Private

    private func runSearch() {
        let query = searchText
        let store = self.store, sort = self.sort
        searchTask?.cancel()
        searchTask = Task { [weak self] in
            let found = await Task.detached(priority: .userInitiated) { () -> [LibrarySearchResult] in
                let notes = LibraryStore.sorted((try? store.notes(in: nil, recursive: true)) ?? [], by: sort)
                return await LibrarySearch.search(query, in: notes, libraryRoot: store.root)
            }.value
            guard let self, !Task.isCancelled, self.searchText == query else { return }
            // Title matches first, each group in the library's sort order.
            self.searchResults = found.filter(\.titleMatch) + found.filter { !$0.titleMatch }
            self.isSearching = false
        }
    }

    private func finishLocationChange() {
        if presentedSheet == .welcome { presentedSheet = nil }
        searchText = ""
        selection = .all                          // didSet refreshes
    }

    /// After `old` moved to `new` (nil: deleted), keeps the selected folder pointing at the same place.
    private func followSelection(from old: URL, to new: URL?) {
        guard let current = currentFolder, SidecarLayout.isInside(current, root: old) else { return }
        guard let new else {
            selection = .all
            return
        }
        let store = self.store
        let tail = store.relativePath(of: current).dropFirst(store.relativePath(of: old).count)
        selection = tail.isEmpty
            ? .folder(new)
            : .folder(store.url(forRelativePath: store.relativePath(of: new) + tail).standardizedFileURL.resolvingSymlinksInPath())
    }

    private func attempt<T>(about url: URL, _ work: () throws -> T) -> T? {
        do {
            return try work()
        } catch {
            report(error, about: url)
            return nil
        }
    }

    private func report(_ error: Error, about url: URL) {
        log.error("Library action failed: \(error.localizedDescription, privacy: .public)")
        // shortcut: ZenithError has no case for a failed file operation; "cannot open this folder" is the closest text.
        self.error = (error as? ZenithError) ?? .accessDenied(url)
    }
}
