import PDFKit
import UIKit

/// Gives PDFKit one overlay per page, keyed by page id, and keeps at most liveCanvasLimit of them off screen.
/// A dropped overlay loses nothing: the ink is in NoteDocument.pages and its undo in the note's undo manager.
@MainActor final class PageOverlayProvider: NSObject, @preconcurrency PDFPageOverlayViewProvider {
    static let liveCanvasLimit = 12               // overlays kept beyond the ones on screen, least recently shown dropped first

    private weak var controller: EditorViewController?
    private(set) var overlays: [UUID: PageOverlayView] = [:]   // by NotePage.id
    /// Overlays PDFKit is displaying now.
    private(set) var onScreen: Set<UUID> = []
    private var lastShown: [UUID: Int] = [:]
    private var clock = 0

    init(controller: EditorViewController) {
        self.controller = controller
        super.init()
    }

    func overlay(forPageID id: UUID) -> PageOverlayView? {
        overlays[id]
    }

    func pdfView(_ view: PDFView, overlayViewFor page: PDFPage) -> UIView? {
        guard let controller, let index = controller.document.index(of: page) else { return nil }
        let id = controller.document.pageID(at: index)
        let size = controller.document.geometry(at: index).displaySize
        if let overlay = overlays[id] {
            overlay.pageSize = size
            stamp(id)
            return overlay
        }
        let overlay = PageOverlayView(pageID: id, pageSize: size)
        controller.prepareOverlay(overlay)
        overlays[id] = overlay
        stamp(id)
        trim()
        return overlay
    }

    func pdfView(_ pdfView: PDFView, willDisplayOverlayView overlayView: UIView, for page: PDFPage) {
        // The page view that holds the overlay must pass touches on, or the canvas never sees one (pitfall P2).
        overlayView.isUserInteractionEnabled = true
        overlayView.superview?.isUserInteractionEnabled = true
        guard let overlay = overlayView as? PageOverlayView else { return }
        let id = overlay.canvas.pageID
        onScreen.insert(id)
        stamp(id)
        controller?.overlayWillDisplay(overlay)
    }

    func pdfView(_ pdfView: PDFView, willEndDisplayingOverlayView overlayView: UIView, for page: PDFPage) {
        guard let overlay = overlayView as? PageOverlayView else { return }
        onScreen.remove(overlay.canvas.pageID)
        overlay.resolution = 1
        trim()
    }

    /// Drops overlays of pages that are no longer in the note.
    func prune(keeping ids: Set<UUID>) {
        for id in overlays.keys where !ids.contains(id) {
            drop(id)
        }
    }

    private func stamp(_ id: UUID) {
        clock += 1
        lastShown[id] = clock
    }

    private func trim() {
        let offScreen = overlays.keys.filter { !onScreen.contains($0) }
        let excess = offScreen.count - Self.liveCanvasLimit
        guard excess > 0 else { return }
        let oldest = offScreen.sorted { (lastShown[$0] ?? 0) < (lastShown[$1] ?? 0) }.prefix(excess)
        for id in oldest {
            drop(id)
        }
    }

    private func drop(_ id: UUID) {
        guard let overlay = overlays.removeValue(forKey: id) else { return }
        onScreen.remove(id)
        lastShown[id] = nil
        controller?.releaseOverlay(overlay)
        overlay.removeFromSuperview()
    }
}
