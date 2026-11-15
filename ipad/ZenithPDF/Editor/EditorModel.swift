import Foundation
import Observation
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
        didSet {}
    }
    var textStyle: TextBoxStyle = TextBoxStyle() {   // saved under SettingKey.textStyle
        didSet {}
    }
    var fingerDrawing: Bool = false {
        didSet {}
    }
    var showsPages: Bool = false
    var showsSearch: Bool = false
    private(set) var currentPage: Int = 0
    private(set) var canUndo: Bool = false
    private(set) var canRedo: Bool = false

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
    }

    func undo() {
    }

    func redo() {
    }

    /// The toolbar's Add Page: addPage(at: currentPage + 1), then go there.
    func addPageAfterCurrent() {
    }
}
