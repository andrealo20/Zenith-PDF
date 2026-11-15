import UIKit

/// Container: canvas resolution, highlights.
@MainActor final class PageOverlayView: UIView {
    let canvas: PageCanvasView
    var pageSize: CGSize                          // display size of the page, points
    var resolution: CGFloat = 1 {                 // the canvas's internal zoom (section 3.5)
        didSet {}
    }
    var highlights: [CGRect] = [] {               // display space
        didSet {}
    }

    init(pageID: UUID, pageSize: CGSize) {
        self.canvas = PageCanvasView(pageID: pageID)
        self.pageSize = pageSize
        super.init(frame: CGRect(origin: .zero, size: pageSize))
    }

    required init?(coder: NSCoder) {
        return nil
    }

    /// Window points per page point, measured in window space: convert(bounds, to: nil).width / pageSize.width.
    var onScreenScale: CGFloat { 1 }

    func displayPoint(fromLocal point: CGPoint) -> CGPoint {
        point
    }

    func localRect(fromDisplay rect: CGRect) -> CGRect {
        rect
    }

    /// Highest internal zoom up to onScreenScale that keeps the canvas bitmap under `budgetBytes` (default 48 MB).
    static func resolution(onScreenScale: CGFloat, pageSize: CGSize, screenScale: CGFloat, budgetBytes: CGFloat = 48_000_000) -> CGFloat {
        1
    }
}
