import PDFKit
import SwiftUI
import UIKit

/// Share, flattened share, print.
@MainActor enum ShareController {
    /// Saves, then returns the file to share: the note itself, or a flattened copy in the temporary folder.
    static func fileToShare(for document: NoteDocument, flattened: Bool) async throws -> URL {
        await document.save()
        // A save that failed leaves the note dirty; sharing the old file would lose what is on screen.
        if document.isDirty, case .cannotSave? = document.lastError, let error = document.lastError { throw error }
        let url = document.url
        guard flattened else { return url }
        let title = document.title
        return try await Task.detached(priority: .userInitiated) { () throws -> URL in
            let data = try FileStore.read(url)
            guard let pdf = PDFDocument(data: data),
                  let flat = pdf.dataRepresentation(options: [PDFDocumentWriteOption.burnInAnnotationsOption.rawValue: true]) else {
                throw ZenithError.cannotSave(url, reason: "The flattened copy could not be made.")
            }
            let folder = FileManager.default.temporaryDirectory
                .appendingPathComponent("Share-" + UUID().uuidString, isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let copy = folder.appendingPathComponent(title + ".pdf", isDirectory: false)
            try flat.write(to: copy, options: .atomic)
            return copy
        }.value
    }

    /// UIPrintInteractionController with the saved file.
    static func print(_ document: NoteDocument) async {
        guard let url = try? await fileToShare(for: document, flattened: false) else { return }
        let controller = UIPrintInteractionController.shared
        let info = UIPrintInfo(dictionary: nil)
        info.jobName = document.title
        info.outputType = .general
        controller.printInfo = info
        controller.printingItem = url
        _ = controller.present(animated: true, completionHandler: nil)
    }
}

struct ShareMenu: View {
    private let document: NoteDocument
    @State private var anchor = ShareAnchor()
    @State private var isWorking = false
    @State private var failure: String?

    init(document: NoteDocument) {
        self.document = document
    }

    var body: some View {
        Menu {
            Button {
                share(flattened: false)
            } label: {
                Label("Share PDF", systemImage: "square.and.arrow.up")
            }
            Button {
                share(flattened: true)
            } label: {
                Label("Share Flattened PDF", systemImage: "doc.richtext")
            }
            Button {
                Task { await ShareController.print(document) }
            } label: {
                Label("Print", systemImage: "printer")
            }
        } label: {
            Label("Share", systemImage: "square.and.arrow.up")
        }
        .disabled(isWorking)
        .background(ShareAnchorView(anchor: anchor))
        .accessibilityIdentifier("shareMenu")
        .alert("Cannot Share", isPresented: Binding(get: { failure != nil }, set: { if !$0 { failure = nil } })) {
            Button("OK", role: .cancel) { failure = nil }
        } message: {
            Text(failure ?? "")
        }
    }

    private func share(flattened: Bool) {
        isWorking = true
        Task {
            defer { isWorking = false }
            do {
                let url = try await ShareController.fileToShare(for: document, flattened: flattened)
                present(url)
            } catch {
                failure = error.localizedDescription
            }
        }
    }

    /// UIActivityViewController as a popover from the menu's button (iPad requires a source view).
    private func present(_ url: URL) {
        guard let source = anchor.view, var presenter = source.window?.rootViewController else { return }
        while let next = presenter.presentedViewController { presenter = next }
        let activity = UIActivityViewController(activityItems: [url], applicationActivities: nil)
        activity.popoverPresentationController?.sourceView = source
        activity.popoverPresentationController?.sourceRect = source.bounds
        presenter.present(activity, animated: true)
    }
}

/// The UIView behind the share button, for the popover's arrow.
private final class ShareAnchor {
    weak var view: UIView?
}

private struct ShareAnchorView: UIViewRepresentable {
    let anchor: ShareAnchor

    func makeUIView(context: Context) -> UIView {
        let view = UIView()
        view.isUserInteractionEnabled = false
        view.backgroundColor = .clear
        anchor.view = view
        return view
    }

    func updateUIView(_ view: UIView, context: Context) {
        anchor.view = view
    }
}
