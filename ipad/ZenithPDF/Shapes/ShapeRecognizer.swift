import CoreGraphics
import Foundation

enum RecognizedShape: Equatable {
    case line(CGPoint, CGPoint)
    case rectangle(CGRect)
    case ellipse(CGRect)
    case polygon([CGPoint])
}

/// Port of inking.as_line / as_shape. Points are in display space; `unit` is one screen pixel in points.
enum ShapeRecognizer {
    /// Port of inking.as_line then inking.as_shape; `unit` is the size of one screen pixel in points (1 / zoom).
    static func recognize(_ points: [CGPoint], unit: CGFloat) -> RecognizedShape? {
        if let ends = asLine(points, unit: unit) {
            return .line(ends.0, ends.1)
        }
        return asShape(points, unit: unit)
    }

    /// (start, end) when the stroke is nearly straight and never doubles back; else nil.
    static func asLine(_ points: [CGPoint], unit: CGFloat) -> (CGPoint, CGPoint)? {
        guard points.count >= 2, let a = points.first, let b = points.last else { return nil }
        let chord = PolylineMath.distance(a, b)
        if chord < 24 * unit || chord <= 0 { return nil }
        var bend: CGFloat = 0
        for p in points {
            bend = max(bend, PolylineMath.segmentDistance(p, a, b))
        }
        if bend > max(0.04 * chord, 3 * unit) { return nil }
        let ux = (b.x - a.x) / chord
        let uy = (b.y - a.y) / chord
        var furthest: CGFloat = 0
        // Along the chord the hand only moves forward, with a little slack.
        for p in points {
            let t = (p.x - a.x) * ux + (p.y - a.y) * uy
            if t < furthest - 4 * unit { return nil }
            furthest = max(furthest, t)
        }
        return (a, b)
    }

    /// What a closed stroke was meant to be. Nil when unsure: better a stroke left as drawn than the wrong shape.
    /// Rotated ellipses stay strokes.
    static func asShape(_ points: [CGPoint], unit: CGFloat) -> RecognizedShape? {
        guard points.count >= 8, let firstPoint = points.first, let lastPoint = points.last else { return nil }
        let perimeter = PolylineMath.pathLength(points)
        let gap = PolylineMath.distance(firstPoint, lastPoint)
        var minX = firstPoint.x, maxX = firstPoint.x, minY = firstPoint.y, maxY = firstPoint.y
        for p in points {
            minX = min(minX, p.x)
            maxX = max(maxX, p.x)
            minY = min(minY, p.y)
            maxY = max(maxY, p.y)
        }
        let w = maxX - minX
        let h = maxY - minY
        if perimeter < 60 * unit || min(w, h) < 10 * unit || gap > max(0.12 * perimeter, 14 * unit) { return nil }
        // The ellipse error divides by the half sizes.
        guard w > 0, h > 0 else { return nil }
        let box = CGRect(x: minX, y: minY, width: w, height: h)
        let centre = CGPoint(x: minX + w / 2, y: minY + h / 2)

        // Corners: start the loop at the point farthest from the centre, close it and simplify.
        var start = 0
        var farthest: CGFloat = -1
        for (index, p) in points.enumerated() {
            let d = PolylineMath.distance(p, centre)
            if d > farthest {
                farthest = d
                start = index
            }
        }
        var loop = Array(points[start...])
        loop.append(contentsOf: points[..<start])
        loop.append(points[start])
        let tolerance = max(0.06 * max(w, h), 2 * unit)
        let kept = PolylineMath.simplifyIndices(loop, tolerance: tolerance).map { loop[$0] }.dropLast()
        let merge = 0.08 * perimeter
        var corners: [CGPoint] = []
        for corner in kept {
            if let previous = corners.last, PolylineMath.distance(corner, previous) <= merge { continue }
            corners.append(corner)
        }
        if corners.count > 2, let first = corners.first, let last = corners.last,
           PolylineMath.distance(first, last) <= merge {
            corners.removeLast()
        }
        let n = corners.count
        var edges: [(CGPoint, CGPoint)] = []
        for i in 0..<n {
            edges.append((corners[i], corners[(i + 1) % n]))
        }

        // How far the stroke is from the ellipse in its bounding box, and from the corners' polygon.
        var error: CGFloat = 0
        for p in points {
            error += abs(hypot((p.x - centre.x) / (w / 2), (p.y - centre.y) / (h / 2)) - 1)
        }
        error /= CGFloat(points.count)
        var off: [CGFloat] = []
        if n >= 3 {
            for p in points {
                var nearest = CGFloat.greatestFiniteMagnitude
                for edge in edges {
                    nearest = min(nearest, PolylineMath.segmentDistance(p, edge.0, edge.1))
                }
                off.append(nearest)
            }
        }
        var polygonError: CGFloat = 1
        if !off.isEmpty {
            polygonError = off.reduce(CGFloat(0), +) / CGFloat(points.count) / (max(w, h) / 2)
        }
        if error < 0.07 && (n > 8 || error < polygonError) {
            return .ellipse(box)
        }
        if n >= 3 && n <= 8, let worst = off.max(), worst <= max(0.08 * max(w, h), 3 * unit) {
            if n == 4 {
                var square = true
                for i in 0..<4 {
                    let corner = angle(corners[(i + 3) % 4], corners[i], corners[(i + 1) % 4])
                    if abs(corner - 90) >= 18 { square = false }
                }
                for edge in edges {
                    let degrees = atan2(edge.1.y - edge.0.y, edge.1.x - edge.0.x) * 180 / CGFloat.pi
                    let tilt = abs(degrees).truncatingRemainder(dividingBy: 90)
                    if !(tilt < 12 || tilt > 78) { square = false }
                }
                if square { return .rectangle(box) }
            }
            return .polygon(corners)
        }
        return nil
    }

    /// The angle at `q`, in degrees.
    private static func angle(_ p: CGPoint, _ q: CGPoint, _ r: CGPoint) -> CGFloat {
        let v1x = p.x - q.x, v1y = p.y - q.y
        let v2x = r.x - q.x, v2y = r.y - q.y
        var lengths = hypot(v1x, v1y) * hypot(v2x, v2y)
        if lengths == 0 { lengths = 1 }
        let cosine = max(-1, min(1, (v1x * v2x + v1y * v2y) / lengths))
        return acos(cosine) * 180 / CGFloat.pi
    }
}
