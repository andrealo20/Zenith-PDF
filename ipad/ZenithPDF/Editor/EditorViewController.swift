import PDFKit
import PencilKit
import UIKit

enum EditorMode: String, CaseIterable, Identifiable { case write, text, select, read; var id: String { rawValue } }

/// A view above the PDF view that lets touches through except on its subviews (text editors, handles).
final class PassThroughView: UIView {
    override func hitTest(_ point: CGPoint, with event: UIEvent?) -> UIView? {
        let hit = super.hitTest(point, with: event)
        return hit === self ? nil : hit
    }
}

/// In write mode the controller itself is first responder and the tool picker is tied to it, so the picker,
/// squeeze and double tap stay up while pages scroll in and out of the window (section 3.5).
@MainActor final class EditorViewController: UIViewController, @preconcurrency PDFViewDelegate, PKCanvasViewDelegate {
    let document: NoteDocument
    let pdfView: PDFView
    let toolPicker: PKToolPicker
    let overlayHost: PassThroughView
    var mode: EditorMode = .write {               // didSet: section 3.5 table
        didSet {}
    }
    var fingerDrawing: Bool = false {             // didSet: drawingPolicy, scroll touches, finger taps
        didSet {}
    }
    private(set) var currentPageIndex: Int = 0
    var isPresenting: Bool { presentationProvider != nil }
    override var canBecomeFirstResponder: Bool { true }
    override var undoManager: UndoManager? { document.undoManager }
    /// Strokes on every page; also published as the PDF view's accessibilityValue "strokes: N" for the UI tests.
    var strokeCount: Int { document.pages.reduce(0) { $0 + $1.drawing.strokes.count } }
    var onPageChange: ((Int) -> Void)?
    var onViewportChange: (() -> Void)?           // scroll or zoom happened
    var onUndoStateChange: (() -> Void)?
    /// A stroke was added to the last page (continuous paper); called in the same event as its undo registration.
    var onLastPageWritten: ((UUID) -> Void)?
    /// Called 0.05 s after the person changed a page's ink (section 3.5); return a replacement or nil.
    var drawingFilter: ((_ pageID: UUID, _ old: PKDrawing, _ new: PKDrawing, _ zoom: CGFloat) -> PKDrawing?)?
    /// Single taps: in .text and .select, and finger taps in .write with finger drawing off. Page, point in PDF
    /// space, point in overlayHost. Return true when handled.
    var tapHandler: ((PDFPage, CGPoint, CGPoint) -> Bool)?
    /// Finger double taps in .write with finger drawing off: page and point in PDF space.
    var doubleTapHandler: ((PDFPage, CGPoint) -> Bool)?
    /// Replay: what each page shows instead of its ink. Setting it (or nil) refreshes every canvas.
    var presentationProvider: ((UUID) -> PKDrawing?)? {
        didSet { refreshPresentations() }
    }
    /// Taps on a page while presenting: page index and point in display space.
    var presentationTapHandler: ((Int, CGPoint) -> Void)?

    init(document: NoteDocument) {
        self.document = document
        self.pdfView = PDFView()
        self.toolPicker = PKToolPicker()
        self.overlayHost = PassThroughView()
        super.init(nibName: nil, bundle: nil)
    }

    required init?(coder: NSCoder) {
        return nil
    }

    func go(toPage index: Int, animated: Bool) {
    }

    func setZoom(_ scale: CGFloat) {
    }

    /// pdfView.scaleFactor
    var zoom: CGFloat { pdfView.scaleFactor }

    /// No-op when that page has no live canvas.
    func reloadCanvas(forPageID id: UUID) {
    }

    func refreshPresentations() {
    }

    /// Display space, by page index; [:] clears.
    func setHighlights(_ rects: [Int: [CGRect]]) {
    }

    func viewRect(forPDFRect rect: CGRect, on page: PDFPage, in view: UIView) -> CGRect {
        rect
    }

    func pdfRect(forViewRect rect: CGRect, in view: UIView, on page: PDFPage) -> CGRect {
        rect
    }

    /// Resigns first responders.
    func endEditing() {
    }

    /// In write mode: becomeFirstResponder and show the tool picker again. Called when the mode returns to write,
    /// when a text view ends editing and when the view appears.
    func reclaimFirstResponder() {
    }
}
