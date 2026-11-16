import Foundation
import PencilKit

/// The drawing filter the editor calls.
@MainActor final class SmartShapes {
    /// Shared by every editor through UserDefaults, so the More menu and Settings toggle the same value.
    var isEnabled: Bool {                         // SettingKey.smartShapes
        get { UserDefaults.standard.bool(forKey: SettingKey.smartShapes) }
        set { UserDefaults.standard.set(newValue, forKey: SettingKey.smartShapes) }
    }

    init() {}

    /// The editor's drawingFilter. Returns nil when disabled, when `new` is not `old` plus exactly one stroke at the
    /// end, when that stroke is a marker stroke or took less than 0.25 s; else `new` with that stroke replaced by
    /// its shape. The editor calls it 0.05 s after the stroke and never while undoing or redoing (section 3.5).
    func filter(pageID: UUID, old: PKDrawing, new: PKDrawing, zoom: CGFloat) -> PKDrawing? {
        guard isEnabled, zoom > 0, zoom.isFinite else { return nil }
        let before = old.strokes
        let after = new.strokes
        guard after.count == before.count + 1, let stroke = after.last else { return nil }
        for (kept, previous) in zip(after, before) where !Self.same(kept, previous) {
            return nil
        }
        guard stroke.ink.inkType != .marker, Self.duration(of: stroke) >= 0.25 else { return nil }
        let pieces = StrokeCodec.polylines(of: stroke)
        guard pieces.count == 1, let piece = pieces.first else { return nil }
        // polylines(of:) is simplified for the file; the recogniser counts points as the desktop samples them.
        let points = Self.resampled(piece.points)
        guard let shape = ShapeRecognizer.recognize(points, unit: 1 / zoom) else { return nil }
        let built = ShapeStrokeBuilder.strokes(for: shape, replacing: stroke)
        guard !built.isEmpty else { return nil }
        return PKDrawing(strokes: before + built)
    }

    /// PKStroke is not relied on to be Equatable: a stroke is the same while its date, points, ink and transform are.
    private static func same(_ a: PKStroke, _ b: PKStroke) -> Bool {
        a.path.creationDate == b.path.creationDate && a.path.count == b.path.count
            && a.ink.inkType == b.ink.inkType && a.transform == b.transform
    }

    private static func duration(of stroke: PKStroke) -> TimeInterval {
        guard let first = stroke.path.first, let last = stroke.path.last else { return 0 }
        return last.timeOffset - first.timeOffset
    }

    /// The polyline with a point every sampleSpacing, as the desktop's tests sample a drawn path.
    private static func resampled(_ points: [CGPoint]) -> [CGPoint] {
        guard let last = points.last else { return [] }
        var out: [CGPoint] = []
        for (p, q) in zip(points, points.dropFirst()) {
            let d = PolylineMath.distance(p, q)
            let n = d.isFinite ? max(1, Int(min(d / StrokeCodec.sampleSpacing, 100_000))) : 1
            for k in 0..<n {
                let f = CGFloat(k) / CGFloat(n)
                out.append(CGPoint(x: p.x + (q.x - p.x) * f, y: p.y + (q.y - p.y) * f))
            }
        }
        out.append(last)
        return out
    }
}
