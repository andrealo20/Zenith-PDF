import Foundation
import PencilKit

/// Strokes by time (pure).
struct ReplayTimeline {
    let recording: Recording

    init(recording: Recording) {
        self.recording = recording
    }

    /// Seconds into the recording when the stroke was started; nil when written outside it.
    func offset(of stroke: PKStroke) -> TimeInterval? {
        nil
    }

    /// The page as it looked `time` seconds in: strokes written later in this recording are drawn at 15% alpha.
    func presentation(of drawing: PKDrawing, at time: TimeInterval) -> PKDrawing {
        drawing
    }

    /// Offset of the recorded stroke nearest to `point` (display space) within `tolerance`, minus 1 s, clamped at 0.
    func seekTime(in drawing: PKDrawing, near point: CGPoint, tolerance: CGFloat) -> TimeInterval? {
        nil
    }
}
