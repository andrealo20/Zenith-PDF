import PDFKit
import UIKit

/// Select, move, resize, delete, duplicate.
@MainActor final class SelectionController {
    private weak var editor: EditorViewController?
    private let textBoxes: TextBoxController
    private(set) var selected: PDFAnnotation?
    private var selectedPage: PDFPage?
    private var outline: SelectionOutlineView?
    /// Bounds the current drag began with; nil while nothing is being dragged.
    private var adjustStart: CGRect?
    private var moveStartFrame: CGRect = .zero
    private var resizeCornerStart: CGPoint = .zero
    private var resizeFixed: CGPoint = .zero
    private let observers = ObserverTokens()

    /// Distance from an object's edge, in page points, that still counts as a tap on it.
    private static let hitSlop: CGFloat = 4
    /// Offset of a duplicate from its original, in page points.
    private static let duplicateOffset: CGFloat = 12
    /// Smallest side a resize leaves, in page points.
    private static let minimumSide: CGFloat = 8

    init(editor: EditorViewController, textBoxes: TextBoxController) {
        self.editor = editor
        self.textBoxes = textBoxes
        let previous = editor.onViewportChange
        editor.onViewportChange = { [weak self] in
            previous?()
            self?.positionOutline()
        }
        let manager = editor.document.undoManager
        for name in [NSNotification.Name.NSUndoManagerDidUndoChange, NSNotification.Name.NSUndoManagerDidRedoChange] {
            let token = NotificationCenter.default.addObserver(forName: name, object: manager, queue: nil) { [weak self] _ in
                MainActor.assumeIsolated { () -> Void in self?.refreshAfterUndo() }
            }
            observers.tokens.append(token)
        }
    }

    /// Text boxes and ImageAnnotations only.
    func handleTap(page: PDFPage, pdfPoint: CGPoint) -> Bool {
        var handled = false
        if textBoxes.editing != nil {
            textBoxes.endEditing()
            handled = true
        }
        if let hit = Self.object(on: page, at: pdfPoint) {
            if hit === selected {
                outline?.presentMenu()
            } else {
                select(hit, on: page)
            }
            return true
        }
        if selected != nil {
            clear()
            return true
        }
        return handled
    }

    func clear() {
        adjustStart = nil
        outline?.removeFromSuperview()
        outline = nil
        selected = nil
        selectedPage = nil
    }

    func deleteSelected() {
        guard let annotation = selected, let page = selectedPage, let document = editor?.document,
              !document.isReadOnly else { return }
        clear()
        Self.changePresence(annotation, on: page, present: false, document: document, actionName: tr("Delete"))
    }

    func duplicateSelected() {
        guard let annotation = selected, let page = selectedPage, let document = editor?.document,
              !document.isReadOnly else { return }
        let box = PageGeometry(page: page).box
        let bounds = Self.clamped(annotation.bounds.offsetBy(dx: Self.duplicateOffset, dy: -Self.duplicateOffset), into: box)
        let copy: PDFAnnotation
        if let picture = annotation as? ImageAnnotation {
            copy = ImageAnnotation(bounds: bounds, imageData: picture.imageData)
        } else {
            copy = TextBoxCodec.makeAnnotation(text: annotation.contents ?? "", style: TextBoxCodec.style(of: annotation),
                                               bounds: bounds)
        }
        Self.changePresence(copy, on: page, present: true, document: document, actionName: tr("Duplicate"))
        select(copy, on: page)
    }

    // MARK: - Dragging, in PDF space (the gestures convert; tests call these directly)

    /// The live part of a drag: the selected object follows, nothing is registered for undo yet.
    func adjust(to bounds: CGRect) {
        guard let annotation = selected else { return }
        if adjustStart == nil { adjustStart = annotation.bounds }
        annotation.bounds = bounds
        positionOutline()
    }

    /// A resize from the corner opposite `fixed` to `moving`; pictures keep their aspect ratio.
    func resizeSelected(fixed: CGPoint, moving: CGPoint) {
        guard let annotation = selected else { return }
        let start = adjustStart ?? annotation.bounds
        adjust(to: Self.resized(start, fixed: fixed, moving: moving, keepAspect: annotation is ImageAnnotation,
                                minimum: Self.minimumSide))
    }

    /// Ends a drag: one undo step back to the bounds it began with.
    func finishAdjusting(actionName: String) {
        guard let start = adjustStart, let annotation = selected, let document = editor?.document else {
            adjustStart = nil
            return
        }
        adjustStart = nil
        let finished = annotation.bounds
        guard finished != start else { return }
        annotation.bounds = start
        Self.changeBounds(annotation, to: finished, document: document, actionName: actionName)
        positionOutline()
    }

    /// The rectangle spanning `fixed` and `moving`, no side under `minimum`; with keepAspect it grows to the
    /// larger of the two scale factors so the shape of `start` is kept.
    static func resized(_ start: CGRect, fixed: CGPoint, moving: CGPoint, keepAspect: Bool, minimum: CGFloat) -> CGRect {
        var width = max(abs(moving.x - fixed.x), minimum)
        var height = max(abs(moving.y - fixed.y), minimum)
        if keepAspect, start.width > 0, start.height > 0 {
            let scale = max(width / start.width, height / start.height)
            width = start.width * scale
            height = start.height * scale
        }
        let x = moving.x >= fixed.x ? fixed.x : fixed.x - width
        let y = moving.y >= fixed.y ? fixed.y : fixed.y - height
        return CGRect(x: x, y: y, width: width, height: height)
    }

    // MARK: - Undoable edits shared by the Content files

    /// Adds or removes `annotation` and registers the opposite change, whose own undo registration gives the redo.
    static func changePresence(_ annotation: PDFAnnotation, on page: PDFPage, present: Bool, document: NoteDocument,
                               actionName: String) {
        if present {
            page.addAnnotation(annotation)
        } else {
            page.removeAnnotation(annotation)
        }
        document.undoManager.registerUndo(withTarget: document) { document in
            MainActor.assumeIsolated { () -> Void in
                changePresence(annotation, on: page, present: !present, document: document, actionName: actionName)
            }
        }
        document.undoManager.setActionName(actionName)
        document.markChanged()
    }

    /// Moves or resizes `annotation` and registers the change back.
    static func changeBounds(_ annotation: PDFAnnotation, to bounds: CGRect, document: NoteDocument, actionName: String) {
        let previous = annotation.bounds
        annotation.bounds = bounds
        document.undoManager.registerUndo(withTarget: document) { document in
            MainActor.assumeIsolated { () -> Void in
                changeBounds(annotation, to: previous, document: document, actionName: actionName)
            }
        }
        document.undoManager.setActionName(actionName)
        document.markChanged()
    }

    /// `rect` moved, not resized, so it lies inside `box` where it fits.
    static func clamped(_ rect: CGRect, into box: CGRect) -> CGRect {
        var result = rect
        result.origin.x = min(max(rect.minX, box.minX), max(box.minX, box.maxX - rect.width))
        result.origin.y = min(max(rect.minY, box.minY), max(box.minY, box.maxY - rect.height))
        return result
    }

    // MARK: - Private

    /// The topmost text box or picture under the point.
    private static func object(on page: PDFPage, at point: CGPoint) -> PDFAnnotation? {
        page.annotations.reversed().first { annotation in
            (annotation is ImageAnnotation || TextBoxCodec.isTextBox(annotation)) && annotation.shouldDisplay
                && annotation.bounds.insetBy(dx: -hitSlop, dy: -hitSlop).contains(point)
        }
    }

    private func select(_ annotation: PDFAnnotation, on page: PDFPage) {
        clear()
        guard let editor else { return }
        selected = annotation
        selectedPage = page
        let view = SelectionOutlineView()
        view.onMove = { [weak self] gesture in self?.handleMove(gesture) }
        view.onResize = { [weak self] corner, gesture in self?.handleResize(corner: corner, gesture) }
        view.menuItems = { [weak self] in self?.menuItems() ?? [] }
        editor.overlayHost.addSubview(view)
        outline = view
        positionOutline()
        view.presentMenu()
    }

    private func positionOutline() {
        guard let editor, let annotation = selected, let page = selectedPage, let outline else { return }
        outline.frame = editor.viewRect(forPDFRect: annotation.bounds, on: page, in: editor.overlayHost)
    }

    private func refreshAfterUndo() {
        guard let annotation = selected else { return }
        if annotation.page == nil {
            clear()
        } else {
            positionOutline()
        }
    }

    private func menuItems() -> [UIMenuElement] {
        guard let annotation = selected, let page = selectedPage else { return [] }
        var items: [UIMenuElement] = []
        if TextBoxCodec.isTextBox(annotation) {
            items.append(UIAction(title: tr("Edit Text"), image: UIImage(systemName: "character.cursor.ibeam")) { [weak self] _ in
                MainActor.assumeIsolated { () -> Void in
                    guard let self else { return }
                    self.clear()
                    self.textBoxes.beginEditing(annotation, on: page)
                }
            })
        }
        items.append(UIAction(title: tr("Duplicate"), image: UIImage(systemName: "plus.square.on.square")) { [weak self] _ in
            MainActor.assumeIsolated { () -> Void in self?.duplicateSelected() }
        })
        items.append(UIAction(title: tr("Delete"), image: UIImage(systemName: "trash"), attributes: .destructive) { [weak self] _ in
            MainActor.assumeIsolated { () -> Void in self?.deleteSelected() }
        })
        return items
    }

    /// A point of overlayHost in the selected page's PDF space.
    private func pdfPoint(_ point: CGPoint) -> CGPoint {
        guard let editor, let page = selectedPage else { return point }
        return editor.pdfRect(forViewRect: CGRect(origin: point, size: .zero), in: editor.overlayHost, on: page).origin
    }

    private func handleMove(_ gesture: UIPanGestureRecognizer) {
        guard let editor, let annotation = selected, let page = selectedPage, let outline else { return }
        switch gesture.state {
        case .began:
            adjustStart = annotation.bounds
            moveStartFrame = outline.frame
        case .changed:
            guard let start = adjustStart else { return }
            let offset = gesture.translation(in: editor.overlayHost)
            let moved = editor.pdfRect(forViewRect: moveStartFrame.offsetBy(dx: offset.x, dy: offset.y),
                                       in: editor.overlayHost, on: page)
            adjust(to: Self.clamped(CGRect(origin: moved.origin, size: start.size), into: PageGeometry(page: page).box))
        default:
            finishAdjusting(actionName: tr("Move"))
        }
    }

    private func handleResize(corner: Int, _ gesture: UIPanGestureRecognizer) {
        guard let editor, let annotation = selected, let outline else { return }
        switch gesture.state {
        case .began:
            adjustStart = annotation.bounds
            let frame = outline.frame
            let corners = [CGPoint(x: frame.minX, y: frame.minY), CGPoint(x: frame.maxX, y: frame.minY),
                           CGPoint(x: frame.maxX, y: frame.maxY), CGPoint(x: frame.minX, y: frame.maxY)]
            resizeCornerStart = corners[corner % 4]
            resizeFixed = Self.nearestCorner(of: annotation.bounds, to: pdfPoint(corners[(corner + 2) % 4]))
        case .changed:
            let offset = gesture.translation(in: editor.overlayHost)
            resizeSelected(fixed: resizeFixed,
                           moving: pdfPoint(CGPoint(x: resizeCornerStart.x + offset.x, y: resizeCornerStart.y + offset.y)))
        default:
            finishAdjusting(actionName: tr("Resize"))
        }
    }

    private static func nearestCorner(of rect: CGRect, to point: CGPoint) -> CGPoint {
        let corners = [CGPoint(x: rect.minX, y: rect.minY), CGPoint(x: rect.maxX, y: rect.minY),
                       CGPoint(x: rect.maxX, y: rect.maxY), CGPoint(x: rect.minX, y: rect.maxY)]
        return corners.min { PolylineMath.distance($0, point) < PolylineMath.distance($1, point) } ?? point
    }
}

/// Notification observers removed when the controller that owns them goes away.
private final class ObserverTokens: @unchecked Sendable {
    var tokens: [NSObjectProtocol] = []

    deinit {
        for token in tokens { NotificationCenter.default.removeObserver(token) }
    }
}

/// Dashed outline with four corner handles, placed in overlayHost over the selected object.
private final class SelectionOutlineView: UIView, UIEditMenuInteractionDelegate {
    var onMove: ((UIPanGestureRecognizer) -> Void)?
    var onResize: ((Int, UIPanGestureRecognizer) -> Void)?
    var menuItems: (() -> [UIMenuElement])?

    private let dash = CAShapeLayer()
    private var movePan: UIPanGestureRecognizer?
    private var handles: [UIView] = []
    private var menu: UIEditMenuInteraction?
    private static let handleTouchSize: CGFloat = 44
    private static let handleDotSize: CGFloat = 12

    override init(frame: CGRect) {
        super.init(frame: frame)
        backgroundColor = .clear
        clipsToBounds = false
        accessibilityIdentifier = "selectionOutline"
        dash.fillColor = UIColor.clear.cgColor
        dash.strokeColor = Folio.UI.accent.resolvedColor(with: UITraitCollection(userInterfaceStyle: .light)).cgColor
        dash.lineWidth = 1.5
        dash.lineDashPattern = [6, 4]
        layer.addSublayer(dash)
        let pan = UIPanGestureRecognizer(target: self, action: #selector(dragOutline(_:)))
        addGestureRecognizer(pan)
        movePan = pan
        addGestureRecognizer(UITapGestureRecognizer(target: self, action: #selector(tapped(_:))))
        for corner in 0..<4 {
            let handle = UIView(frame: CGRect(x: 0, y: 0, width: Self.handleTouchSize, height: Self.handleTouchSize))
            handle.tag = corner
            let dot = CALayer()
            let inset = (Self.handleTouchSize - Self.handleDotSize) / 2
            dot.frame = CGRect(x: inset, y: inset, width: Self.handleDotSize, height: Self.handleDotSize)
            dot.cornerRadius = Self.handleDotSize / 2
            dot.backgroundColor = UIColor.white.cgColor
            dot.borderColor = dash.strokeColor
            dot.borderWidth = 1.5
            handle.layer.addSublayer(dot)
            handle.addGestureRecognizer(UIPanGestureRecognizer(target: self, action: #selector(resize(_:))))
            addSubview(handle)
            handles.append(handle)
        }
        let interaction = UIEditMenuInteraction(delegate: self)
        addInteraction(interaction)
        menu = interaction
    }

    required init?(coder: NSCoder) {
        return nil
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        dash.frame = bounds
        dash.path = UIBezierPath(rect: bounds).cgPath
        let corners = [CGPoint(x: 0, y: 0), CGPoint(x: bounds.width, y: 0),
                       CGPoint(x: bounds.width, y: bounds.height), CGPoint(x: 0, y: bounds.height)]
        for (handle, corner) in zip(handles, corners) {
            handle.center = corner
        }
    }

    /// The handles reach past the outline's edges; touches there still belong to them.
    override func point(inside point: CGPoint, with event: UIEvent?) -> Bool {
        bounds.insetBy(dx: -Self.handleTouchSize / 2, dy: -Self.handleTouchSize / 2).contains(point)
    }

    /// A drag that starts on a handle resizes; it never also moves the outline.
    override func gestureRecognizerShouldBegin(_ gestureRecognizer: UIGestureRecognizer) -> Bool {
        if gestureRecognizer === movePan {
            let point = gestureRecognizer.location(in: self)
            if handles.contains(where: { $0.frame.contains(point) }) { return false }
        }
        return super.gestureRecognizerShouldBegin(gestureRecognizer)
    }

    func presentMenu() {
        guard window != nil, let menu else { return }
        menu.presentEditMenu(with: UIEditMenuConfiguration(identifier: nil, sourcePoint: CGPoint(x: bounds.midX, y: 0)))
    }

    func editMenuInteraction(_ interaction: UIEditMenuInteraction, menuFor configuration: UIEditMenuConfiguration,
                             suggestedActions: [UIMenuElement]) -> UIMenu? {
        UIMenu(children: menuItems?() ?? [])
    }

    @objc private func tapped(_ gesture: UITapGestureRecognizer) {
        presentMenu()
    }

    @objc private func dragOutline(_ gesture: UIPanGestureRecognizer) {
        onMove?(gesture)
    }

    @objc private func resize(_ gesture: UIPanGestureRecognizer) {
        onResize?(gesture.view?.tag ?? 0, gesture)
    }
}
