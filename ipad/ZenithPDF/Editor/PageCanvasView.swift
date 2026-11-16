import PencilKit
import UIKit

/// One page's PencilKit canvas. Its drawing coordinates are the page's display space (section 3.5).
@MainActor final class PageCanvasView: PKCanvasView {
    let pageID: UUID
    private let pencilKitUndo = UndoManager()

    /// A private UndoManager per canvas. PencilKit registers its own actions there; they are never used and are
    /// cleared after every change, because the note's undo is registered by NoteDocument.setDrawing (section 3.5).
    override var undoManager: UndoManager? { pencilKitUndo }

    var suppressesChanges: Bool = false           // true while code assigns `drawing`

    init(pageID: UUID) {
        self.pageID = pageID
        super.init(frame: .zero)
        backgroundColor = .clear
        isOpaque = false
        isScrollEnabled = false
        showsVerticalScrollIndicator = false
        showsHorizontalScrollIndicator = false
        bouncesZoom = false
        contentInsetAdjustmentBehavior = .never
        // PencilKit turns black ink white in dark mode; paper is always light (pitfall P7).
        overrideUserInterfaceStyle = .light
        maximumSupportedContentVersion = .latest
    }

    required init?(coder: NSCoder) {
        return nil
    }

    /// Assigns with suppressesChanges.
    func setDrawingSilently(_ drawing: PKDrawing) {
        suppressesChanges = true
        self.drawing = drawing
        suppressesChanges = false
        pencilKitUndo.removeAllActions()
    }

    /// removeAllActions() on the private manager.
    func clearPencilKitUndo() {
        pencilKitUndo.removeAllActions()
    }
}
