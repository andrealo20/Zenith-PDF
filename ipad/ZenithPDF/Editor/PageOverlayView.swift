import UIKit

/// The view PDFKit places over one page: holds the page's canvas at its internal resolution, and search highlights.
@MainActor final class PageOverlayView: UIView {
    let canvas: PageCanvasView
    var pageSize: CGSize {                        // display size of the page, points
        didSet { if pageSize != oldValue { setNeedsLayout() } }
    }
    var resolution: CGFloat = 1 {                 // the canvas's internal zoom (section 3.5)
        didSet { if resolution != oldValue { setNeedsLayout() } }
    }
    var highlights: [CGRect] = [] {               // display space
        didSet { if highlights != oldValue { updateHighlights() } }
    }
    /// Only exists while there are highlights, so a page without any carries no extra layer (pitfall P4).
    private(set) var highlightLayer: CAShapeLayer?

    init(pageID: UUID, pageSize: CGSize) {
        self.canvas = PageCanvasView(pageID: pageID)
        self.pageSize = pageSize
        super.init(frame: CGRect(origin: .zero, size: pageSize))
        backgroundColor = .clear
        isOpaque = false
        overrideUserInterfaceStyle = .light
        addSubview(canvas)
    }

    required init?(coder: NSCoder) {
        return nil
    }

    /// Window points per page point, measured in window space: convert(bounds, to: nil).width / pageSize.width.
    var onScreenScale: CGFloat {
        guard pageSize.width > 0 else { return 1 }
        let scale = convert(bounds, to: nil).width / pageSize.width
        return scale.isFinite ? scale : 1
    }

    func displayPoint(fromLocal point: CGPoint) -> CGPoint {
        guard bounds.width > 0, bounds.height > 0 else { return point }
        return CGPoint(x: point.x * pageSize.width / bounds.width, y: point.y * pageSize.height / bounds.height)
    }

    func localRect(fromDisplay rect: CGRect) -> CGRect {
        guard pageSize.width > 0, pageSize.height > 0 else { return rect }
        let sx = bounds.width / pageSize.width, sy = bounds.height / pageSize.height
        return CGRect(x: rect.minX * sx, y: rect.minY * sy, width: rect.width * sx, height: rect.height * sy)
    }

    /// Highest internal zoom up to onScreenScale that keeps the canvas bitmap under `budgetBytes` (default 48 MB).
    static func resolution(onScreenScale: CGFloat, pageSize: CGSize, screenScale: CGFloat, budgetBytes: CGFloat = 48_000_000) -> CGFloat {
        let wanted = max(onScreenScale.isFinite ? onScreenScale : 1, 1)
        let pixels = 4 * pageSize.width * pageSize.height * screenScale * screenScale
        guard pixels > 0, pixels.isFinite else { return wanted }
        return min(wanted, (budgetBytes / pixels).squareRoot())
    }

    override func layoutSubviews() {
        super.layoutSubviews()
        let s = resolution
        let size = CGSize(width: pageSize.width * s, height: pageSize.height * s)
        guard size.width > 0, size.height > 0, bounds.width > 0, bounds.height > 0 else { return }
        // Bounds and center only: frame is undefined under a transform (pitfall P10).
        canvas.transform = .identity
        canvas.bounds = CGRect(origin: .zero, size: size)
        canvas.minimumZoomScale = s
        canvas.maximumZoomScale = s
        canvas.zoomScale = s
        canvas.contentSize = size
        canvas.contentOffset = .zero
        canvas.transform = CGAffineTransform(scaleX: bounds.width / size.width, y: bounds.height / size.height)
        canvas.center = CGPoint(x: bounds.midX, y: bounds.midY)
        layoutHighlights()
    }

    private func updateHighlights() {
        if highlights.isEmpty {
            highlightLayer?.removeFromSuperlayer()
            highlightLayer = nil
            return
        }
        if highlightLayer == nil {
            let layer = CAShapeLayer()
            layer.fillColor = UIColor(red: 1, green: 0.84, blue: 0.2, alpha: 0.35).cgColor
            layer.strokeColor = nil
            self.layer.insertSublayer(layer, below: canvas.layer)
            highlightLayer = layer
        }
        layoutHighlights()
    }

    private func layoutHighlights() {
        guard let highlightLayer else { return }
        highlightLayer.frame = bounds
        let path = UIBezierPath()
        for rect in highlights {
            path.append(UIBezierPath(roundedRect: localRect(fromDisplay: rect).insetBy(dx: -1, dy: -1), cornerRadius: 2))
        }
        highlightLayer.path = path.cgPath
    }
}
