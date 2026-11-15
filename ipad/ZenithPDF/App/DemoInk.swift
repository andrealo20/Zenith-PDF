import Foundation
import PencilKit

/// Handwriting-like strokes from glyph outlines, and their word rects.
enum DemoInk {
    /// Handwriting-like strokes: each glyph outline of `text` in a rounded system font, flattened and sampled.
    static func strokes(_ text: String, at origin: CGPoint, size: CGFloat, ink: PKInk, startingAt date: Date, secondsPerStroke: TimeInterval) -> [PKStroke] {
        []
    }

    /// One rect per word of `text`, display space, matching what strokes(_:at:size:...) draws.
    static func words(_ text: String, at origin: CGPoint, size: CGFloat) -> [InkWord] {
        []
    }

    static func underline(from: CGPoint, to: CGPoint, ink: PKInk, date: Date) -> PKStroke {
        let points = [from, to].enumerated().map { item in
            PKStrokePoint(location: item.element, timeOffset: TimeInterval(item.offset) * 0.1,
                          size: CGSize(width: 2, height: 2), opacity: 1, force: 1, azimuth: 0, altitude: .pi / 2)
        }
        return PKStroke(ink: ink, path: PKStrokePath(controlPoints: points, creationDate: date),
                        transform: .identity, mask: nil)
    }
}
