import CoreGraphics
import Foundation
import PencilKit

/// Clean strokes for a shape.
enum ShapeStrokeBuilder {
    private static let spacing: CGFloat = 2
    private static let ellipseSegments = 96

    private struct TracePoint {
        let point: CGPoint
        let corner: Bool
    }

    /// Strokes tracing the shape with the original's ink, mean width and creation date.
    static func strokes(for shape: RecognizedShape, replacing original: PKStroke) -> [PKStroke] {
        let trace: [TracePoint]
        switch shape {
        case let .line(a, b):
            trace = sampled([a, b])
        case let .rectangle(r):
            trace = sampled([CGPoint(x: r.minX, y: r.minY), CGPoint(x: r.maxX, y: r.minY),
                             CGPoint(x: r.maxX, y: r.maxY), CGPoint(x: r.minX, y: r.maxY),
                             CGPoint(x: r.minX, y: r.minY)])
        case let .ellipse(r):
            trace = ellipse(in: r)
        case let .polygon(corners):
            guard let first = corners.first else { return [original] }
            trace = sampled(corners + [first])
        }
        let source = Array(original.path)
        guard trace.count >= 2, let firstSource = source.first, let lastSource = source.last else { return [original] }

        var width: CGFloat = 0, opacity: CGFloat = 0, force: CGFloat = 0
        for point in source {
            width += point.size.width
            opacity += point.opacity
            force += point.force
        }
        let count = CGFloat(source.count)
        let t = original.transform
        // The new stroke has no transform, so the original's scale goes into the width.
        let size = max(width / count * sqrt(abs(t.a * t.d - t.b * t.c)), 0.1)

        // A corner three times in a row is a point the uniform cubic B-spline passes through.
        var locations: [CGPoint] = []
        for item in trace {
            locations.append(contentsOf: Array(repeating: item.point, count: item.corner ? 3 : 1))
        }
        let duration = max(lastSource.timeOffset - firstSource.timeOffset, 0)
        let step = duration / TimeInterval(max(locations.count - 1, 1))
        var controlPoints: [PKStrokePoint] = []
        controlPoints.reserveCapacity(locations.count)
        for (index, location) in locations.enumerated() {
            controlPoints.append(PKStrokePoint(location: location, timeOffset: step * TimeInterval(index),
                                               size: CGSize(width: size, height: size), opacity: opacity / count,
                                               force: force / count, azimuth: 0, altitude: .pi / 2))
        }
        let path = PKStrokePath(controlPoints: controlPoints, creationDate: original.path.creationDate)
        return [PKStroke(ink: original.ink, path: path, transform: .identity, mask: nil)]
    }

    /// The polyline through `vertices` with a point every `spacing` or less; the vertices are corners.
    private static func sampled(_ vertices: [CGPoint]) -> [TracePoint] {
        guard let last = vertices.last else { return [] }
        var out: [TracePoint] = []
        for i in 0..<(vertices.count - 1) {
            let p = vertices[i], q = vertices[i + 1]
            out.append(TracePoint(point: p, corner: true))
            let d = PolylineMath.distance(p, q)
            let n = d.isFinite ? max(1, Int(min((d / spacing).rounded(.up), 100_000))) : 1
            if n > 1 {
                for k in 1..<n {
                    let f = CGFloat(k) / CGFloat(n)
                    out.append(TracePoint(point: CGPoint(x: p.x + (q.x - p.x) * f, y: p.y + (q.y - p.y) * f),
                                          corner: false))
                }
            }
        }
        out.append(TracePoint(point: last, corner: true))
        return out
    }

    /// The ellipse in `r` in `ellipseSegments` segments, starting and ending at its rightmost point.
    private static func ellipse(in r: CGRect) -> [TracePoint] {
        let start = CGPoint(x: r.maxX, y: r.midY)
        var out = [TracePoint(point: start, corner: true)]
        for k in 1..<ellipseSegments {
            let angle = 2 * CGFloat.pi * CGFloat(k) / CGFloat(ellipseSegments)
            out.append(TracePoint(point: CGPoint(x: r.midX + r.width / 2 * cos(angle),
                                                 y: r.midY + r.height / 2 * sin(angle)), corner: false))
        }
        out.append(TracePoint(point: start, corner: true))
        return out
    }
}
