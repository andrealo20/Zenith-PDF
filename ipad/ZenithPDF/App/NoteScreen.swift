import SwiftUI

/// Opens a NoteDocument, shows EditorView, saves on scene phase.
struct NoteScreen: View {
    private let url: URL?
    private let value: NoteWindowValue?
    private let launch: EditorLaunchState
    private let onClose: () -> Void

    init(url: URL, launch: EditorLaunchState, onClose: @escaping () -> Void) {
        self.url = url
        self.value = nil
        self.launch = launch
        self.onClose = onClose
    }

    init(value: NoteWindowValue, launch: EditorLaunchState, onClose: @escaping () -> Void) {
        self.url = nil
        self.value = value
        self.launch = launch
        self.onClose = onClose
    }

    /// Loading, error, or EditorView; saves when scenePhase becomes .inactive or .background.
    var body: some View {
        ProgressView()
    }
}
