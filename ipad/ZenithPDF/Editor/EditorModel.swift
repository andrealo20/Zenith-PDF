import Foundation
import Observation
import PencilKit
import UIKit

/// Demo scenes, UI tests and state restoration.
struct EditorLaunchState {
    var page: Int = 0
    var mode: EditorMode = .write
    var showsPages = false
    var showsSearch = false
    var searchQuery: String? = nil
    var zoom: CGFloat? = nil
    var fingerDrawing: Bool? = nil                 // overrides the setting for this editor (UI tests)
    var replayAt: TimeInterval? = nil              // plays the first recording, seeks, pauses

    init() {}
}

/// Editor state and feature controllers.
@MainActor @Observable final class EditorModel {
    let document: NoteDocument
    let controller: EditorViewController
    let textBoxes: TextBoxController
    let selection: SelectionController
    let shapes: SmartShapes
    let audio: AudioController
    let search: NoteSearchModel
    var mode: EditorMode = .write {
        didSet {
            guard !isLoading, mode != oldValue else { return }
            // A text box being edited is committed, and a selection cleared, before the canvases take over.
            textBoxes.endEditing()
            selection.clear()
            controller.mode = mode
        }
    }
    var textStyle: TextBoxStyle = TextBoxStyle() {   // saved under SettingKey.textStyle
        didSet {
            guard !isLoading else { return }
            textBoxes.style = textStyle
            if let data = try? JSONEncoder().encode(textStyle) {
                UserDefaults.standard.set(data, forKey: SettingKey.textStyle)
            }
        }
    }
    var fingerDrawing: Bool = false {
        didSet {
            guard !isLoading else { return }
            controller.fingerDrawing = fingerDrawing
            UserDefaults.standard.set(fingerDrawing, forKey: SettingKey.fingerDrawing)
        }
    }
    /// Shape recognition, mirrored here so the More menu redraws when it changes.
    var smartShapes: Bool = false {
        didSet { if !isLoading { shapes.isEnabled = smartShapes } }
    }
    var showsPages: Bool = false
    var showsSearch: Bool = false
    private(set) var currentPage: Int = 0
    private(set) var canUndo: Bool = false
    private(set) var canRedo: Bool = false
    /// True until init ends: the initial values are not changes the person made, so didSet neither saves nor applies them.
    @ObservationIgnored private var isLoading = true

    private static let editorScenes: Set<DemoScene> = [.note, .darkPaper, .zoomed, .pages, .noteSearch, .audio, .desktopNote]

    init(document: NoteDocument, launch: EditorLaunchState = EditorLaunchState()) {
        let controller = EditorViewController(document: document)
        let textBoxes = TextBoxController(editor: controller)
        self.document = document
        self.controller = controller
        self.textBoxes = textBoxes
        self.selection = SelectionController(editor: controller, textBoxes: textBoxes)
        self.shapes = SmartShapes()
        self.audio = AudioController(document: document, editor: controller)
        self.search = NoteSearchModel(document: document, editor: controller)
        self.showsPages = launch.showsPages
        self.showsSearch = launch.showsSearch

        // Each initial value goes to its controller by hand; a launch override is never saved as the setting.
        let defaults = UserDefaults.standard
        if let data = defaults.data(forKey: SettingKey.textStyle),
           let saved = try? JSONDecoder().decode(TextBoxStyle.self, from: data) {
            textStyle = saved
        }
        textBoxes.style = textStyle
        fingerDrawing = launch.fingerDrawing ?? LaunchOptions.fingerDrawing ?? defaults.bool(forKey: SettingKey.fingerDrawing)
        controller.fingerDrawing = fingerDrawing
        smartShapes = shapes.isEnabled
        mode = document.isReadOnly ? .read : launch.mode
        controller.mode = mode
        currentPage = min(max(launch.page, 0), max(document.pages.count - 1, 0))

        let shapes = self.shapes
        controller.drawingFilter = { pageID, old, new, zoom in
            shapes.filter(pageID: pageID, old: old, new: new, zoom: zoom)
        }
        controller.tapHandler = { [weak self] page, pdfPoint, _ in
            guard let self else { return false }
            switch self.mode {
            case .text: return self.textBoxes.handleTap(page: page, pdfPoint: pdfPoint)
            case .select, .write: return self.selection.handleTap(page: page, pdfPoint: pdfPoint)
            case .read: return false
            }
        }
        textBoxes.onStyleChange = { [weak self] style in self?.textStyle = style }
        controller.doubleTapHandler = { [weak self] page, pdfPoint in
            self?.textBoxes.editExisting(page: page, pdfPoint: pdfPoint) ?? false
        }
        controller.onLastPageWritten = { [weak document] id in
            document?.appendPageIfLast(id)
        }
        controller.onPageChange = { [weak self] index in
            self?.currentPage = index
            self?.document.lastPage = index
        }
        controller.onUndoStateChange = { [weak self] in
            self?.refreshUndoState()
        }
        document.onSaved = { [weak document] in
            guard let document else { return }
            SearchIndexer.shared.schedule(document)
            let url = document.url
            Task { await ThumbnailCache.shared.invalidate(url) }
        }
        controller.onFirstLayout = { [weak self] in
            Task { @MainActor in await self?.apply(launch) }
        }
        refreshUndoState()
        isLoading = false
    }

    func undo() {
        textBoxes.endEditing()
        if document.undoManager.canUndo { document.undoManager.undo() }
        refreshUndoState()
    }

    func redo() {
        textBoxes.endEditing()
        if document.undoManager.canRedo { document.undoManager.redo() }
        refreshUndoState()
    }

    /// The toolbar's Add Page: addPage(at: currentPage + 1), then go there.
    func addPageAfterCurrent() {
        let index = min(currentPage + 1, document.pages.count)
        document.addPage(at: index)
        controller.go(toPage: index, animated: true)
        refreshUndoState()
    }

    private func refreshUndoState() {
        canUndo = document.undoManager.canUndo
        canRedo = document.undoManager.canRedo
    }

    /// The launch state, once the PDF view has its size; then the ready marker of an editor demo scene.
    private func apply(_ launch: EditorLaunchState) async {
        if let zoom = launch.zoom { controller.setZoom(zoom) }
        if launch.page > 0 { controller.go(toPage: currentPage, animated: false) }
        if let query = launch.searchQuery {
            showsSearch = true
            search.query = query
            await search.run()
        }
        if let time = launch.replayAt, let first = audio.recordings.first {
            audio.play(first)
            audio.seek(to: time)
            audio.pause()
        }
        controller.pdfView.layoutIfNeeded()
        // One more turn of the run loop, so PDFKit has put the overlays of the pages on screen.
        await Task.yield()
        if let scene = LaunchOptions.demoScene, Self.editorScenes.contains(scene) {
            LaunchOptions.markReady(scene.rawValue)
        }
    }
}
