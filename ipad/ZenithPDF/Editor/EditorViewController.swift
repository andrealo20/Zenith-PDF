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
@MainActor final class EditorViewController: UIViewController, PDFViewDelegate, PKCanvasViewDelegate {
    let document: NoteDocument
    let pdfView: PDFView
    let toolPicker: PKToolPicker
    let overlayHost: PassThroughView
    var mode: EditorMode = .write {               // didSet: section 3.5 table
        didSet { if mode != oldValue { applyMode() } }
    }
    var fingerDrawing: Bool = false {             // didSet: drawingPolicy, scroll touches, finger taps
        didSet { if fingerDrawing != oldValue { applyMode() } }
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
    /// Runs once, after the first layout that gave the PDF view a size (EditorModel applies its launch state).
    var onFirstLayout: (() -> Void)?

    /// Strongly held: PDFView keeps its provider weakly (pitfall P1).
    private(set) lazy var overlayProvider: PageOverlayProvider = PageOverlayProvider(controller: self)
    private(set) var singleTap = UITapGestureRecognizer()
    private(set) var doubleTap = UITapGestureRecognizer()
    private(set) var twoFingerTap = UITapGestureRecognizer()
    private weak var scrollView: UIScrollView?
    private var offsetObservation: NSKeyValueObservation?
    /// Each recognizer's own allowedTouchTypes, restored when leaving write mode (pitfall P14).
    private var defaultTouchTypes: [ObjectIdentifier: [NSNumber]] = [:]
    private var highlightRects: [Int: [CGRect]] = [:]
    private var didFirstLayout = false
    private var appliedPresenting = false
    /// The page whose canvas is being stored into the document: its reload would only reassign what it shows.
    private var storingPageID: UUID?
    private let resolutionDebouncer = Debouncer(seconds: 0.15)

    init(document: NoteDocument) {
        self.document = document
        self.pdfView = PDFView()
        self.toolPicker = PKToolPicker()
        self.overlayHost = PassThroughView()
        super.init(nibName: nil, bundle: nil)
        document.onDrawingReplaced = { [weak self] id in self?.drawingReplaced(id) }
        document.onPagesChanged = { [weak self] in self?.pagesChanged() }
    }

    required init?(coder: NSCoder) {
        return nil
    }

    // MARK: - View lifecycle

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = Folio.UI.canvas

        pdfView.frame = view.bounds
        pdfView.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        pdfView.displayMode = .singlePageContinuous
        pdfView.displayDirection = .vertical
        pdfView.autoScales = true
        pdfView.pageShadowsEnabled = true
        pdfView.backgroundColor = Folio.UI.canvas
        pdfView.displaysPageBreaks = true
        pdfView.maxScaleFactor = 6
        pdfView.delegate = self
        pdfView.isInMarkupMode = mode != .read
        pdfView.accessibilityIdentifier = "editor"
        // Provider first, then the document, or PDFKit never asks for overlays (pitfall P1).
        pdfView.pageOverlayViewProvider = overlayProvider
        pdfView.document = document.pdf
        view.addSubview(pdfView)

        overlayHost.frame = view.bounds
        overlayHost.autoresizingMask = [.flexibleWidth, .flexibleHeight]
        overlayHost.backgroundColor = .clear
        view.addSubview(overlayHost)

        toolPicker.stateAutosaveName = "zenith.tools"
        toolPicker.colorUserInterfaceStyle = .light
        toolPicker.showsDrawingPolicyControls = false
        toolPicker.maximumSupportedContentVersion = .latest

        installTapRecognizers()
        let center = NotificationCenter.default
        center.addObserver(self, selector: #selector(pdfPageChanged(_:)), name: .PDFViewPageChanged, object: pdfView)
        center.addObserver(self, selector: #selector(pdfScaleChanged(_:)), name: .PDFViewScaleChanged, object: pdfView)
        for name in [Notification.Name.NSUndoManagerDidCloseUndoGroup, .NSUndoManagerDidUndoChange, .NSUndoManagerDidRedoChange] {
            center.addObserver(self, selector: #selector(undoStateChanged(_:)), name: name, object: document.undoManager)
        }
        attachScrollView()
        appliedPresenting = isPresenting
        applyMode()
        publishStrokeCount()
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        attachScrollView()
        applyScrollTouches()
        guard !didFirstLayout, pdfView.bounds.width > 0, pdfView.bounds.height > 0 else { return }
        didFirstLayout = true
        let fit = pdfView.scaleFactorForSizeToFit
        if fit > 0 { pdfView.minScaleFactor = fit / 2 }
        updateResolutions()
        onFirstLayout?()
        onFirstLayout = nil
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        reclaimFirstResponder()
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        toolPicker.setVisible(false, forFirstResponder: self)
    }

    // MARK: - Public interface

    func go(toPage index: Int, animated: Bool) {
        // PDFView has no animated page jump; `animated` is accepted for callers that may get one later.
        guard let page = document.pdf.page(at: index) else { return }
        if isViewLoaded, pdfView.document != nil {
            pdfView.go(to: page)
        }
        setCurrentPage(index)
    }

    /// Scrolls so that a display-space point of a page sits at the top-left of the view.
    func show(displayPoint point: CGPoint, onPage index: Int) {
        guard let page = document.pdf.page(at: index) else { return }
        pdfView.go(to: PDFDestination(page: page, at: document.geometry(at: index).toPDF(point)))
    }

    func setZoom(_ scale: CGFloat) {
        guard scale > 0, scale.isFinite else { return }
        pdfView.autoScales = false
        pdfView.scaleFactor = min(max(scale, pdfView.minScaleFactor), pdfView.maxScaleFactor)
        resolutionDebouncer.schedule { [weak self] in self?.updateResolutions() }
    }

    /// pdfView.scaleFactor
    var zoom: CGFloat { pdfView.scaleFactor }

    /// No-op when that page has no live canvas.
    func reloadCanvas(forPageID id: UUID) {
        guard let overlay = overlayProvider.overlay(forPageID: id) else { return }
        overlay.canvas.setDrawingSilently(shownDrawing(for: id))
    }

    func refreshPresentations() {
        for (id, overlay) in overlayProvider.overlays {
            overlay.canvas.setDrawingSilently(shownDrawing(for: id))
        }
        // The provider changes at 10 Hz during replay; the mode table only when presenting starts or stops.
        if isPresenting != appliedPresenting {
            appliedPresenting = isPresenting
            applyMode()
        }
    }

    /// Display space, by page index; [:] clears.
    func setHighlights(_ rects: [Int: [CGRect]]) {
        highlightRects = rects
        for overlay in overlayProvider.overlays.values {
            applyHighlights(to: overlay)
        }
    }

    func viewRect(forPDFRect rect: CGRect, on page: PDFPage, in view: UIView) -> CGRect {
        let inPDFView = pdfView.convert(rect, from: page)
        return view.convert(inPDFView, from: pdfView)
    }

    func pdfRect(forViewRect rect: CGRect, in view: UIView, on page: PDFPage) -> CGRect {
        let inPDFView = view.convert(rect, to: pdfView)
        return pdfView.convert(inPDFView, to: page)
    }

    /// Resigns first responders.
    func endEditing() {
        guard isViewLoaded else { return }
        view.endEditing(true)
    }

    /// In write mode: becomeFirstResponder and show the tool picker again. Called when the mode returns to write,
    /// when a text view ends editing and when the view appears.
    func reclaimFirstResponder() {
        guard isViewLoaded, mode == .write, !isPresenting else { return }
        toolPicker.setVisible(true, forFirstResponder: self)
        if view.window != nil, !isFirstResponder {
            becomeFirstResponder()
        }
    }

    // MARK: - Canvases (called by PageOverlayProvider)

    /// A new overlay: its canvas gets the delegate, the page's ink, the tool picker and the mode's settings.
    func prepareOverlay(_ overlay: PageOverlayView) {
        let canvas = overlay.canvas
        canvas.delegate = self
        canvas.drawingPolicy = fingerDrawing ? .anyInput : .pencilOnly
        canvas.setDrawingSilently(shownDrawing(for: canvas.pageID))
        toolPicker.addObserver(canvas)
        canvas.tool = toolPicker.selectedToolItem.tool ?? PKInkingTool(.pen, color: .black, width: 2)
        canvas.isUserInteractionEnabled = isWriting
        applyHighlights(to: overlay)
    }

    func overlayWillDisplay(_ overlay: PageOverlayView) {
        overlay.canvas.isUserInteractionEnabled = isWriting
        overlay.resolution = resolution(for: overlay)
    }

    func releaseOverlay(_ overlay: PageOverlayView) {
        toolPicker.removeObserver(overlay.canvas)
        overlay.canvas.delegate = nil
    }

    // MARK: - PKCanvasViewDelegate

    func canvasViewDrawingDidChange(_ canvasView: PKCanvasView) {
        let undo = document.undoManager
        guard let canvas = canvasView as? PageCanvasView, !canvas.suppressesChanges, !isPresenting,
              !undo.isUndoing, !undo.isRedoing, document.index(ofPageID: canvas.pageID) != nil else { return }
        let id = canvas.pageID
        let old = document.drawing(forPageID: id)
        let new = PencilKitWidth.fromPencilKit(canvas.drawing)
        storingPageID = id
        document.setDrawing(new, forPageID: id, undoable: true)
        storingPageID = nil
        canvas.clearPencilKitUndo()
        // Same event as the stroke's undo registration, so one undo group holds both (section 3.5).
        if new.strokes.count > old.strokes.count, document.pages.last?.id == id {
            onLastPageWritten?(id)
        }
        if drawingFilter != nil {
            let count = new.strokes.count
            let lastDate = new.strokes.last?.path.creationDate
            let zoom = self.zoom
            // The delay lets the run loop go idle, which closes the stroke's undo group first.
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.05) { [weak self] in
                MainActor.assumeIsolated { () -> Void in
                    self?.runDrawingFilter(pageID: id, old: old, count: count, lastDate: lastDate, zoom: zoom)
                }
            }
        }
        publishStrokeCount()
        onUndoStateChange?()
    }

    // MARK: - Gestures

    /// A finger tap at `point` in the PDF view's coordinates.
    func routeTap(at point: CGPoint) {
        guard let page = pdfView.page(for: point, nearest: false), let index = document.index(of: page) else { return }
        let pdfPoint = pdfView.convert(point, to: page)
        if isPresenting {
            presentationTapHandler?(index, document.geometry(at: index).toDisplay(pdfPoint))
            return
        }
        switch mode {
        case .text, .select:
            _ = tapHandler?(page, pdfPoint, overlayHost.convert(point, from: pdfView))
        case .write where !fingerDrawing:
            _ = tapHandler?(page, pdfPoint, overlayHost.convert(point, from: pdfView))
        default:
            break
        }
    }

    func routeDoubleTap(at point: CGPoint) {
        guard mode == .write, !fingerDrawing, !isPresenting,
              let page = pdfView.page(for: point, nearest: false) else { return }
        _ = doubleTapHandler?(page, pdfView.convert(point, to: page))
    }

    /// The two-finger tap: undo in write, text and select modes.
    func undoFromGesture() {
        guard mode != .read, !isPresenting else { return }
        endEditing()
        if document.undoManager.canUndo { document.undoManager.undo() }
        reclaimFirstResponder()
        onUndoStateChange?()
    }

    /// The first finger of a two-finger scroll may have started a stroke: cancel it so it leaves no mark (pitfall P6).
    func cancelStrokesInProgress() {
        for overlay in overlayProvider.overlays.values {
            let recognizer = overlay.canvas.drawingGestureRecognizer
            recognizer.isEnabled = false
            recognizer.isEnabled = true
        }
    }

    // MARK: - Private

    private var isWriting: Bool { mode == .write && !isPresenting }

    private func shownDrawing(for id: UUID) -> PKDrawing {
        PencilKitWidth.forPencilKit(presentationProvider?(id) ?? document.drawing(forPageID: id))
    }

    private func applyMode() {
        guard isViewLoaded else { return }
        let writing = isWriting
        pdfView.isInMarkupMode = mode != .read || isPresenting
        for overlay in overlayProvider.overlays.values {
            overlay.canvas.isUserInteractionEnabled = writing
            overlay.canvas.drawingPolicy = fingerDrawing ? .anyInput : .pencilOnly
        }
        applyScrollTouches()
        singleTap.isEnabled = isPresenting || mode == .text || mode == .select || (mode == .write && !fingerDrawing)
        doubleTap.isEnabled = writing && !fingerDrawing
        twoFingerTap.isEnabled = !isPresenting && mode != .read
        if writing {
            reclaimFirstResponder()
        } else {
            toolPicker.setVisible(false, forFirstResponder: self)
        }
    }

    /// The mode table's touch types and touch counts on the PDF scroll view (pitfalls P6, P14).
    private func applyScrollTouches() {
        guard let scrollView else { return }
        let writing = isWriting
        var recognizers: [UIGestureRecognizer] = [scrollView.panGestureRecognizer]
        if let pinch = scrollView.pinchGestureRecognizer { recognizers.append(pinch) }
        let direct = [NSNumber(value: UITouch.TouchType.direct.rawValue)]
        for recognizer in recognizers {
            let key = ObjectIdentifier(recognizer)
            if defaultTouchTypes[key] == nil { defaultTouchTypes[key] = recognizer.allowedTouchTypes }
            let wanted = writing ? direct : (defaultTouchTypes[key] ?? recognizer.allowedTouchTypes)
            if recognizer.allowedTouchTypes != wanted { recognizer.allowedTouchTypes = wanted }
        }
        scrollView.panGestureRecognizer.minimumNumberOfTouches = writing && fingerDrawing ? 2 : 1
    }

    private func attachScrollView() {
        let found = (pdfView.documentView?.superview as? UIScrollView)
            ?? pdfView.subviews.compactMap { $0 as? UIScrollView }.first
        guard let found, found !== scrollView else { return }
        scrollView = found
        found.panGestureRecognizer.addTarget(self, action: #selector(scrollPanned(_:)))
        offsetObservation = found.observe(\.contentOffset, options: []) { [weak self] _, _ in
            MainActor.assumeIsolated { () -> Void in self?.onViewportChange?() }
        }
    }

    private func installTapRecognizers() {
        let direct = [NSNumber(value: UITouch.TouchType.direct.rawValue)]
        singleTap = UITapGestureRecognizer(target: self, action: #selector(singleTapped(_:)))
        doubleTap = UITapGestureRecognizer(target: self, action: #selector(doubleTapped(_:)))
        doubleTap.numberOfTapsRequired = 2
        twoFingerTap = UITapGestureRecognizer(target: self, action: #selector(twoFingerTapped(_:)))
        twoFingerTap.numberOfTouchesRequired = 2
        for recognizer in [singleTap, doubleTap, twoFingerTap] {
            recognizer.allowedTouchTypes = direct
            recognizer.delegate = self
            pdfView.addGestureRecognizer(recognizer)
        }
    }

    private func applyHighlights(to overlay: PageOverlayView) {
        guard let index = document.index(ofPageID: overlay.canvas.pageID) else { return }
        overlay.highlights = highlightRects[index] ?? []
    }

    private func resolution(for overlay: PageOverlayView) -> CGFloat {
        let screenScale = traitCollection.displayScale > 0 ? traitCollection.displayScale : 2
        return PageOverlayView.resolution(onScreenScale: overlay.onScreenScale, pageSize: overlay.pageSize,
                                          screenScale: screenScale)
    }

    private func updateResolutions() {
        for (id, overlay) in overlayProvider.overlays {
            overlay.resolution = overlayProvider.onScreen.contains(id) ? resolution(for: overlay) : 1
        }
    }

    private func runDrawingFilter(pageID id: UUID, old: PKDrawing, count: Int, lastDate: Date?, zoom: CGFloat) {
        let undo = document.undoManager
        guard let drawingFilter, !undo.isUndoing, !undo.isRedoing else { return }
        let current = document.drawing(forPageID: id)
        // Only the drawing just stored: an undo, another stroke or a reload since then means the moment has passed.
        guard current.strokes.count == count, current.strokes.last?.path.creationDate == lastDate,
              let replacement = drawingFilter(id, old, current, zoom) else { return }
        document.setDrawing(replacement, forPageID: id, undoable: true, actionName: tr("Shape"))
        onUndoStateChange?()
    }

    private func drawingReplaced(_ id: UUID) {
        if id != storingPageID { reloadCanvas(forPageID: id) }
        publishStrokeCount()
    }

    private func pagesChanged() {
        overlayProvider.prune(keeping: Set(document.pages.map(\.id)))
        if isViewLoaded { pdfView.layoutDocumentView() }
        for overlay in overlayProvider.overlays.values {
            applyHighlights(to: overlay)
        }
        if let page = pdfView.currentPage, let index = document.index(of: page) {
            setCurrentPage(index)
        } else {
            setCurrentPage(min(currentPageIndex, max(document.pages.count - 1, 0)))
        }
        publishStrokeCount()
        onUndoStateChange?()
    }

    private func setCurrentPage(_ index: Int) {
        guard index != currentPageIndex else { return }
        currentPageIndex = index
        onPageChange?(index)
    }

    private func publishStrokeCount() {
        pdfView.accessibilityValue = "strokes: \(strokeCount)"
    }

    @objc private func pdfPageChanged(_ notification: Notification) {
        guard let page = pdfView.currentPage, let index = document.index(of: page) else { return }
        setCurrentPage(index)
    }

    @objc private func pdfScaleChanged(_ notification: Notification) {
        onViewportChange?()
        resolutionDebouncer.schedule { [weak self] in self?.updateResolutions() }
    }

    @objc private func undoStateChanged(_ notification: Notification) {
        onUndoStateChange?()
    }

    @objc private func scrollPanned(_ pan: UIPanGestureRecognizer) {
        if pan.state == .began, fingerDrawing, isWriting { cancelStrokesInProgress() }
    }

    @objc private func singleTapped(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended else { return }
        routeTap(at: recognizer.location(in: pdfView))
    }

    @objc private func doubleTapped(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended else { return }
        routeDoubleTap(at: recognizer.location(in: pdfView))
    }

    @objc private func twoFingerTapped(_ recognizer: UITapGestureRecognizer) {
        guard recognizer.state == .ended else { return }
        undoFromGesture()
    }
}

extension EditorViewController: UIGestureRecognizerDelegate {
    /// Our taps work beside PDFKit's own recognizers instead of waiting for them.
    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                           shouldRecognizeSimultaneouslyWith otherGestureRecognizer: UIGestureRecognizer) -> Bool {
        true
    }

    /// The single tap waits for the double tap only while the double tap can happen.
    func gestureRecognizer(_ gestureRecognizer: UIGestureRecognizer,
                           shouldRequireFailureOf otherGestureRecognizer: UIGestureRecognizer) -> Bool {
        gestureRecognizer === singleTap && otherGestureRecognizer === doubleTap && doubleTap.isEnabled
    }
}
