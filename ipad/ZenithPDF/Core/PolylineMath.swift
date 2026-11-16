import CoreGraphics

enum PolylineMath {
    static func distance(_ p: CGPoint, _ q: CGPoint) -> CGFloat {
        hypot(q.x - p.x, q.y - p.y)
    }

    static func segmentDistance(_ p: CGPoint, _ a: CGPoint, _ b: CGPoint) -> CGFloat {
        let dx = b.x - a.x, dy = b.y - a.y
        let length = dx * dx + dy * dy
        let t: CGFloat = length == 0 ? 0 : max(0, min(1, ((p.x - a.x) * dx + (p.y - a.y) * dy) / length))
        return hypot(p.x - (a.x + t * dx), p.y - (a.y + t * dy))
    }

    static func pathLength(_ points: [CGPoint]) -> CGFloat {
        guard points.count > 1 else { return 0 }
        var total: CGFloat = 0
        for i in 1..<points.count { total += distance(points[i - 1], points[i]) }
        return total
    }

    /// Indices kept by Ramer-Douglas-Peucker (port of inking.simplify_indices), sorted, first and last always kept.
    static func simplifyIndices(_ points: [CGPoint], tolerance: CGFloat) -> [Int] {
        guard points.count >= 3 else { return Array(points.indices) }
        var keep: Set<Int> = [0, points.count - 1]
        var stack: [(Int, Int)] = [(0, points.count - 1)]
        while let range = stack.popLast() {
            let first = range.0, last = range.1
            let a = points[first], b = points[last]
            let length = hypot(b.x - a.x, b.y - a.y)
            var worst: CGFloat = 0
            var index = -1
            if last - first > 1 {
                for i in (first + 1)..<last {
                    let p = points[i]
                    let d = length == 0
                        ? hypot(p.x - a.x, p.y - a.y)
                        : abs((b.x - a.x) * (a.y - p.y) - (a.x - p.x) * (b.y - a.y)) / length
                    if d > worst {
                        worst = d
                        index = i
                    }
                }
            }
            if index > 0 && worst > tolerance {
                keep.insert(index)
                stack.append((first, index))
                stack.append((index, last))
            }
        }
        return keep.sorted()
    }
}
