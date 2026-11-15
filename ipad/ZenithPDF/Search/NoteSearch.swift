import Observation
import SwiftUI

/// In-note search model.
@MainActor @Observable final class NoteSearchModel {
    private let document: NoteDocument
    private let editor: EditorViewController

    init(document: NoteDocument, editor: EditorViewController) {
        self.document = document
        self.editor = editor
    }

    var query: String = ""
    private(set) var hits: [SearchHit] = []
    private(set) var isIndexing: Bool = false

    /// indexNow if stale, then hits; highlights all hits.
    func run() async {
    }

    /// Go to page, highlight that hit.
    func select(_ hit: SearchHit) {
    }

    func clear() {
    }
}

/// In-note search panel.
struct NoteSearchPanel: View {
    private let model: NoteSearchModel

    init(model: NoteSearchModel) {
        self.model = model
    }

    var body: some View {
        EmptyView()
    }
}
