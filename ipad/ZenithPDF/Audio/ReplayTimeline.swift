import Foundation
import PencilKit
import UIKit

/// Strokes by time (pure).
struct ReplayTimeline {
    let recording: Recording

    private static let fadedAlpha: CGFloat = 0.15

    init(recording: Recording) {
        self.recording = recording
    }

    /// Seconds into the recording when the stroke was started; nil when written outside it.
    func offset(of stroke: PKStroke) -> TimeInterval? {
        let offset = stroke.path.creationDate.timeIntervalSince(recording.startedAt)
        return offset >= 0 && offset <= recording.duration ? offset : nil
    }

    /// The page as it looked `time` seconds in: strokes written later in this recording are drawn at 15% alpha.
    func presentation(of drawing: PKDrawing, at time: TimeInterval) -> PKDrawing {
        let strokes = drawing.strokes.map { stroke -> PKStroke in
            guard let offset = offset(of: stroke), offset > time else { return stroke }
            let color = stroke.ink.color
            let ink = PKInk(stroke.ink.inkType, color: color.withAlphaComponent(color.cgColor.alpha * Self.fadedAlpha))
            return PKStroke(ink: ink, path: stroke.path, transform: stroke.transform, mask: stroke.mask,
                            randomSeed: stroke.randomSeed)
        }
        return PKDrawing(strokes: strokes)
    }

    /// Offset of the recorded stroke nearest to `point` (display space) within `tolerance`, minus 1 s, clamped at 0.
    func seekTime(in drawing: PKDrawing, near point: CGPoint, tolerance: CGFloat) -> TimeInterval? {
        var best: (distance: CGFloat, offset: TimeInterval)?
        for stroke in drawing.strokes {
            guard let offset = offset(of: stroke) else { continue }
            let distance = Self.distance(from: point, to: stroke)
            if distance <= tolerance && distance < (best?.distance ?? .infinity) {
                best = (distance, offset)
            }
        }
        guard let best else { return nil }
        return max(best.offset - 1, 0)
    }

    /// Distance to the stroke's centre line, in drawing coordinates.
    private static func distance(from point: CGPoint, to stroke: PKStroke) -> CGFloat {
        var points = stroke.path.interpolatedPoints(by: .distance(2)).map { $0.location.applying(stroke.transform) }
        if points.isEmpty {
            points = stroke.path.map { $0.location.applying(stroke.transform) }
        }
        guard let first = points.first else { return .infinity }
        guard points.count > 1 else { return PolylineMath.distance(point, first) }
        var nearest = CGFloat.infinity
        for index in 1..<points.count {
            nearest = min(nearest, PolylineMath.segmentDistance(point, points[index - 1], points[index]))
        }
        return nearest
    }
}
