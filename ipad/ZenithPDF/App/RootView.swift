import SwiftUI

/// Owns LibraryModel; .fullScreenCover(item:) of OpenNote; .onOpenURL;
/// WelcomeSheet while LibraryLocation.shared.needsChoice.
struct RootView: View {
    @State private var model = LibraryModel(location: LibraryLocation.shared)
    @State private var openNote: OpenNote? = nil
    @State private var editorLaunch = LaunchOptions.baseEditorLaunch()
    @State private var started = false
    /// A demo scene that starts in a note shows nothing beneath it, so only the editor can say the scene is drawn.
    @State private var hidesLibrary = false
    @Environment(\.openWindow) private var openWindow
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        if LaunchOptions.isUnitTesting {
            // Unit tests are hosted in the app: nothing to load, so they start at once.
            Color.clear
        } else {
            content
        }
    }

    private var content: some View {
        Group {
            if started && !hidesLibrary {
                LibraryView(model: model, open: { url, newWindow in open(url, newWindow: newWindow) })
            } else {
                Folio.window.ignoresSafeArea()
            }
        }
        .tint(Folio.accent)
        .fullScreenCover(item: $openNote, onDismiss: {
            hidesLibrary = false
            model.refresh()
        }) { note in
            NoteScreen(url: note.url, launch: editorLaunch, onClose: { openNote = nil })
                .tint(Folio.accent)
        }
        .onOpenURL { url in
            _ = LaunchOptions.prepareLaunch()
            if let target = OpenURLHandler.handle(url, library: model) { open(target, newWindow: false) }
        }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .inactive || phase == .background else { return }
            Task { await NoteRegistry.shared.saveAll() }
        }
        .task { await start() }
    }

    private func open(_ url: URL, newWindow: Bool) {
        if newWindow {
            openWindow(id: "note", value: NoteWindowValue(url: url, libraryRoot: LibraryLocation.shared.root))
        } else {
            editorLaunch = LaunchOptions.baseEditorLaunch()
            openNote = OpenNote(url: url)
        }
    }

    private func start() async {
        guard !started else { return }
        let demo = LaunchOptions.prepareLaunch()
        guard let demo, let scene = LaunchOptions.demoScene else {
            let needsChoice = LibraryLocation.shared.needsChoice
            LaunchOptions.expectReady("launch", waitsForContent: false, presentation: needsChoice)
            model.refresh()
            started = true
            if needsChoice { model.presentedSheet = .welcome }
            LaunchOptions.markShellReady()
            return
        }
        model.selection = demo.selection
        model.refresh()
        if let note = demo.openNote {
            LaunchOptions.expectReady(scene.rawValue, waitsForContent: true, presentation: true)
            hidesLibrary = true
            started = true
            editorLaunch = demo.editor
            openNote = OpenNote(url: note)
            // NoteScreen marks the shell ready once the note is open; the editor says when its pages are shown.
            return
        }
        // The library's views say when they are drawn: the grid once its thumbnails show, the sheets on appearing,
        // the trash and the search results once listed.
        LaunchOptions.expectReady(scene.rawValue, waitsForContent: true, presentation: demo.sheet != nil)
        started = true
        if !demo.searchText.isEmpty { model.searchText = demo.searchText }
        if let sheet = demo.sheet { model.presentedSheet = sheet }
        await RootView.warmThumbnails(root: demo.root)
        if !demo.searchText.isEmpty {
            // The library debounces its search; wait for the results, at most 10 s.
            var tries = 0
            while model.searchResults.isEmpty && tries < 100 {
                try? await Task.sleep(nanoseconds: 100_000_000)
                tries += 1
            }
        }
        LaunchOptions.markShellReady()
    }

    /// Renders every thumbnail into the cache, so the grid draws them on its first pass.
    private static func warmThumbnails(root: URL) async {
        let notes = (try? LibraryStore(root: root).notes(in: nil, recursive: true)) ?? []
        for note in notes {
            _ = await ThumbnailCache.shared.thumbnail(for: note.url, modified: note.modified, width: 400)
        }
    }
}
