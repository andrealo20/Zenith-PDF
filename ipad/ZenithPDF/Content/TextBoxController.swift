import PDFKit
import UIKit

/// Create and edit text boxes.
@MainActor final class TextBoxController: NSObject, UITextViewDelegate {
    private weak var editor: EditorViewController?
    var style: TextBoxStyle = TextBoxStyle() {    // applied to new boxes and to the one being edited
        didSet {
            if style != oldValue { restyleTextView() }
        }
    }
    private(set) var editing: PDFAnnotation?
    /// The text view over the box being edited, in editor.overlayHost.
    private(set) var textView: UITextView?
    /// Called when editing an existing box takes over that box's style, so the style bar can show it.
    var onStyleChange: ((TextBoxStyle) -> Void)?

    private var editingPage: PDFPage?
    private var isNew = false

    private static let defaultWidth: CGFloat = 240
    /// The desktop's TEXT_PADDING, page points between the box edge and its text.
    private static let padding: CGFloat = 4
    /// Distance from a box's edge, in page points, that still counts as a tap on it.
    private static let hitSlop: CGFloat = 4

    init(editor: EditorViewController) {
        self.editor = editor
        super.init()
        let previous = editor.onViewportChange
        editor.onViewportChange = { [weak self] in
            previous?()
            self?.viewportChanged()
        }
    }

    /// .text mode: edits the text box under the point, else creates one there (default width 240 pt).
    func handleTap(page: PDFPage, pdfPoint: CGPoint) -> Bool {
        guard let document = editor?.document, !document.isReadOnly else { return false }
        let wasEditing = editing != nil
        if let hit = Self.textBox(on: page, at: pdfPoint) {
            endEditing()
            beginEditing(hit, on: page)
            return true
        }
        endEditing()
        // The first tap away from a box only finishes it.
        if wasEditing { return true }
        let box = PageGeometry(page: page).box
        let width = min(Self.defaultWidth, box.width)
        let topLeft = CGPoint(x: min(max(pdfPoint.x, box.minX), max(box.minX, box.maxX - width)), y: pdfPoint.y)
        let bounds = TextBoxCodec.fittedBounds(text: "", style: style, topLeft: topLeft, width: width)
        beginEditing(TextBoxCodec.makeAnnotation(text: "", style: style, bounds: bounds), on: page)
        return true
    }

    /// Finger double tap in .write: edits the text box under the point; never creates one.
    func editExisting(page: PDFPage, pdfPoint: CGPoint) -> Bool {
        guard let document = editor?.document, !document.isReadOnly,
              let hit = Self.textBox(on: page, at: pdfPoint) else { return false }
        endEditing()
        beginEditing(hit, on: page)
        return true
    }

    func beginEditing(_ annotation: PDFAnnotation, on page: PDFPage) {
        guard let editor, !editor.document.isReadOnly else { return }
        if editing != nil { endEditing() }
        editing = annotation
        editingPage = page
        // A box not on the page yet is a new one: it goes on the page when it is committed with some text.
        isNew = annotation.page == nil
        if !isNew {
            let adopted = TextBoxCodec.style(of: annotation)
            style = adopted
            onStyleChange?(adopted)
            // The text view shows the text while it is edited; PDFKit would draw it a second time underneath.
            annotation.shouldDisplay = false
        }
        let view = UITextView(frame: editor.viewRect(forPDFRect: annotation.bounds, on: page, in: editor.overlayHost))
        view.text = annotation.contents ?? ""
        view.backgroundColor = .clear
        view.overrideUserInterfaceStyle = .light
        view.isScrollEnabled = false
        view.textContainer.lineFragmentPadding = 0
        view.layer.borderWidth = 1
        view.layer.borderColor = Folio.UI.accent.resolvedColor(with: UITraitCollection(userInterfaceStyle: .light)).cgColor
        view.accessibilityIdentifier = "textBoxEditor"
        view.delegate = self
        editor.overlayHost.addSubview(view)
        textView = view
        restyleTextView()
        view.becomeFirstResponder()
    }

    /// Commits through TextBoxCodec.apply; an empty new box is removed; editor.reclaimFirstResponder().
    func endEditing() {
        guard let annotation = editing, let page = editingPage else { return }
        let text = textView?.text ?? ""
        let view = textView
        let wasNew = isNew
        // State is cleared first: removing the view resigns it, which calls back into textViewDidEndEditing.
        editing = nil
        editingPage = nil
        textView = nil
        isNew = false
        view?.delegate = nil
        view?.removeFromSuperview()
        annotation.shouldDisplay = true
        if let document = editor?.document {
            commit(text: text, annotation: annotation, page: page, isNew: wasNew, document: document)
        }
        editor?.reclaimFirstResponder()
    }

    // MARK: - UITextViewDelegate

    func textViewDidChange(_ textView: UITextView) {
        fitTextViewHeight()
    }

    func textViewDidEndEditing(_ textView: UITextView) {
        if textView === self.textView { endEditing() }
    }

    // MARK: - Private

    private struct TextState {
        var text: String
        var style: TextBoxStyle
        var bounds: CGRect
    }

    private func commit(text: String, annotation: PDFAnnotation, page: PDFPage, isNew: Bool, document: NoteDocument) {
        let empty = text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        if empty {
            // A new box with nothing in it never existed; an existing one emptied is deleted.
            if !isNew {
                SelectionController.changePresence(annotation, on: page, present: false, document: document,
                                                   actionName: tr("Delete"))
            }
            return
        }
        let old = annotation.bounds
        let bounds = TextBoxCodec.fittedBounds(text: text, style: style, topLeft: CGPoint(x: old.minX, y: old.maxY),
                                               width: old.width)
        let state = TextState(text: text, style: style, bounds: bounds)
        if isNew {
            TextBoxCodec.apply(text: text, style: style, to: annotation)
            annotation.bounds = bounds
            SelectionController.changePresence(annotation, on: page, present: true, document: document,
                                               actionName: tr("Add Text Box"))
            return
        }
        guard text != (annotation.contents ?? "") || style != TextBoxCodec.style(of: annotation) else { return }
        Self.change(annotation, to: state, document: document, actionName: tr("Edit Text"))
    }

    /// Applies `state` and registers the change back to what the box held before.
    private static func change(_ annotation: PDFAnnotation, to state: TextState, document: NoteDocument, actionName: String) {
        let previous = TextState(text: annotation.contents ?? "", style: TextBoxCodec.style(of: annotation),
                                 bounds: annotation.bounds)
        TextBoxCodec.apply(text: state.text, style: state.style, to: annotation)
        annotation.bounds = state.bounds
        document.undoManager.registerUndo(withTarget: document) { document in
            MainActor.assumeIsolated { () -> Void in
                change(annotation, to: previous, document: document, actionName: actionName)
            }
        }
        document.undoManager.setActionName(actionName)
        document.markChanged()
    }

    /// The topmost text box under the point that is shown.
    private static func textBox(on page: PDFPage, at point: CGPoint) -> PDFAnnotation? {
        page.annotations.reversed().first { annotation in
            TextBoxCodec.isTextBox(annotation) && annotation.shouldDisplay
                && annotation.bounds.insetBy(dx: -hitSlop, dy: -hitSlop).contains(point)
        }
    }

    /// Page points to overlay points: the PDF view's scale factor.
    private var zoom: CGFloat {
        let value = editor?.zoom ?? 1
        return value.isFinite && value > 0.05 ? value : 1
    }

    private func restyleTextView() {
        guard let textView else { return }
        let scale = zoom
        textView.font = style.uiFont.withSize(style.size * scale)
        textView.textColor = style.color.uiColor
        textView.textAlignment = NSTextAlignment(rawValue: style.alignment) ?? .left
        let inset = Self.padding * scale
        textView.textContainerInset = UIEdgeInsets(top: inset, left: inset, bottom: inset, right: inset)
        fitTextViewHeight()
    }

    /// Grows the text view with its text, never below the box it started from.
    private func fitTextViewHeight() {
        guard let textView, let editor, let annotation = editing, let page = editingPage else { return }
        let base = editor.viewRect(forPDFRect: annotation.bounds, on: page, in: editor.overlayHost)
        let fit = textView.sizeThatFits(CGSize(width: textView.frame.width, height: .greatestFiniteMagnitude))
        var frame = textView.frame
        frame.size.height = max(fit.height, base.height)
        textView.frame = frame
    }

    /// The box is committed once the page moves under it; a resize that leaves it in place (the keyboard
    /// appearing) does not count.
    private func viewportChanged() {
        guard let editor, let textView, let annotation = editing, let page = editingPage else { return }
        let now = editor.viewRect(forPDFRect: annotation.bounds, on: page, in: editor.overlayHost)
        if abs(now.minX - textView.frame.minX) > 1 || abs(now.minY - textView.frame.minY) > 1
            || abs(now.width - textView.frame.width) > 1 {
            endEditing()
        }
    }
}
