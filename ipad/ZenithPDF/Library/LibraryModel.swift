import Foundation
import Observation
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

/// Library state and actions.
@MainActor @Observable final class LibraryModel {
    let location: LibraryLocation

    init(location: LibraryLocation) {
        self.location = location
    }

    var store: LibraryStore { LibraryStore(root: location.root) }
    var trashStore: TrashStore { TrashStore(library: store) }
    var selection: SidebarSelection = .all {      // refreshes
        didSet { refresh() }
    }
    var sort: LibrarySort = .modified {           // SettingKey.sort
        didSet {}
    }
    var presentedSheet: LibrarySheet? = nil
    var searchText: String = "" {                 // runs the search, 0.3 s debounce
        didSet {}
    }
    private(set) var subjects: [FolderItem] = []  // top-level folders, for the sidebar
    private(set) var folders: [FolderItem] = []   // inside the selected folder
    private(set) var notes: [NoteItem] = []
    private(set) var trash: [TrashedItem] = []
    private(set) var searchResults: [LibrarySearchResult] = []
    var error: ZenithError? = nil

    /// nil unless selection is .folder
    var currentFolder: URL? {
        if case .folder(let url) = selection { return url }
        return nil
    }

    func refresh() {
    }

    func createSubject(named name: String) {
    }

    /// In currentFolder.
    func createNote(template: PaperTemplate, name: String?) -> URL? {
        nil
    }

    func importPDFs(_ urls: [URL]) -> [URL] {
        []
    }

    func createNote(fromImages images: [UIImage]) -> URL? {
        nil
    }

    func rename(_ url: URL, to name: String) {
    }

    func duplicate(_ url: URL) {
    }

    func move(_ url: URL, to folder: URL?) {
    }

    func toggleFavourite(_ note: NoteItem) {
    }

    func delete(_ url: URL) {
    }

    func restore(_ item: TrashedItem) {
    }

    func deletePermanently(_ item: TrashedItem) {
    }

    func emptyTrash() {
    }
}
