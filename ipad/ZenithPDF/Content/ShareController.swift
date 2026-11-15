import SwiftUI
import UIKit

/// Share, flattened share, print.
@MainActor enum ShareController {
    /// Saves, then returns the file to share: the note itself, or a flattened copy in the temporary folder.
    static func fileToShare(for document: NoteDocument, flattened: Bool) async throws -> URL {
        document.url
    }

    /// UIPrintInteractionController with the saved file.
    static func print(_ document: NoteDocument) async {
    }
}

struct ShareMenu: View {
    private let document: NoteDocument

    init(document: NoteDocument) {
        self.document = document
    }

    var body: some View {
        EmptyView()
    }
}
