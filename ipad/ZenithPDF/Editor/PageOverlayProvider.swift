import PDFKit
import UIKit

/// PDFPageOverlayViewProvider, bounded canvas cache.
@MainActor final class PageOverlayProvider: NSObject, @preconcurrency PDFPageOverlayViewProvider {
    static let liveCanvasLimit = 12               // overlays kept beyond the ones on screen, least recently shown dropped first

    private weak var controller: EditorViewController?
    private(set) var overlays: [UUID: PageOverlayView] = [:]   // by NotePage.id

    init(controller: EditorViewController) {
        self.controller = controller
        super.init()
    }

    func overlay(forPageID id: UUID) -> PageOverlayView? {
        overlays[id]
    }

    func pdfView(_ view: PDFView, overlayViewFor page: PDFPage) -> UIView? {
        nil
    }

    func pdfView(_ pdfView: PDFView, willDisplayOverlayView overlayView: UIView, for page: PDFPage) {
    }

    func pdfView(_ pdfView: PDFView, willEndDisplayingOverlayView overlayView: UIView, for page: PDFPage) {
    }
}
