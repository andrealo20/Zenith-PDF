import Foundation
import PencilKit

/// The drawing filter the editor calls.
@MainActor final class SmartShapes {
    var isEnabled: Bool = false                   // SettingKey.smartShapes

    init() {}

    /// The editor's drawingFilter. Returns nil when disabled, when `new` is not `old` plus exactly one stroke at the
    /// end, when that stroke is a marker stroke or took less than 0.25 s; else `new` with that stroke replaced by
    /// its shape. The editor calls it 0.05 s after the stroke and never while undoing or redoing (section 3.5).
    func filter(pageID: UUID, old: PKDrawing, new: PKDrawing, zoom: CGFloat) -> PKDrawing? {
        nil
    }
}
