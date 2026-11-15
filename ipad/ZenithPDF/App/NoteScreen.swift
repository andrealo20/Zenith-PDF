import SwiftUI

/// Opens a NoteDocument, shows EditorView, saves on scene phase.
struct NoteScreen: View {
    private let url: URL?
    private let value: NoteWindowValue?
    private let launch: EditorLaunchState
    private let onClose: () -> Void

    @State private var document: NoteDocument? = nil
    @State private var failure: String? = nil
    @Environment(\.scenePhase) private var scenePhase

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
        Group {
            if let document {
                EditorView(document: document, launch: launch, onClose: { close() })
            } else if let failure {
                failureView(failure)
            } else {
                ProgressView()
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .background(Folio.canvas)
            }
        }
        .task { await load() }
        .onChange(of: scenePhase) { _, phase in
            guard phase == .inactive || phase == .background, let document else { return }
            Task { await document.save() }
        }
        .onDisappear {
            guard let document else { return }
            Task { await NoteRegistry.shared.close(document) }
        }
    }

    private func failureView(_ message: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "exclamationmark.triangle")
                .font(.system(size: 40))
                .foregroundStyle(Folio.textMuted)
            Text(verbatim: message)
                .multilineTextAlignment(.center)
                .foregroundStyle(Folio.text)
            Button("Close") { onClose() }
                .buttonStyle(.borderedProminent)
        }
        .padding(32)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(Folio.window)
    }

    private func load() async {
        guard document == nil, failure == nil else { return }
        _ = LaunchOptions.prepareLaunch()
        let root = LibraryLocation.shared.root
        guard let target = url ?? value?.resolve(libraryRoot: root) else {
            failure = tr("The note could not be opened.")
            return
        }
        let libraryRoot: URL? = SidecarLayout.isInside(target, root: root) ? root : nil
        do {
            let opened = try await NoteRegistry.shared.open(target, libraryRoot: libraryRoot)
            if Task.isCancelled {
                // The screen went away while the note was loading.
                await NoteRegistry.shared.close(opened)
                return
            }
            document = opened
            LaunchOptions.markShellReady()
        } catch {
            log.error("Note not opened: \(error.localizedDescription, privacy: .public)")
            if NoteRegistry.shared.isOpen(target) {
                failure = tr("This note is open in another window.")
            } else {
                failure = (error as? ZenithError)?.errorDescription ?? tr("The note could not be opened.")
            }
        }
    }

    private func close() {
        if let document {
            Task { await NoteRegistry.shared.close(document) }
        }
        onClose()
    }
}
