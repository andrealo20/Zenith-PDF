import PencilKit
import UIKit
import XCTest
@testable import ZenithPDF

/// Smart shapes: the desktop's smart ink cases, the strokes built for a shape and the editor's filter.
final class ShapeTests: XCTestCase {
    private var savedSetting: Any?

    override func setUp() {
        super.setUp()
        savedSetting = UserDefaults.standard.object(forKey: SettingKey.smartShapes)
    }

    override func tearDown() {
        if let savedSetting {
            UserDefaults.standard.set(savedSetting, forKey: SettingKey.smartShapes)
        } else {
            UserDefaults.standard.removeObject(forKey: SettingKey.smartShapes)
        }
        super.tearDown()
    }

    // MARK: Recogniser

    func testRecognisesWhatWasMeant() {
        var rng = ShapeRandom(seed: 4)
        for item in Drawn.cases() {
            var results: [String: Int] = [:]
            for _ in 0..<40 {
                let points = Drawn.jitter(item.make(), amount: 1.2, rng: &rng)
                let kind = Drawn.kind(of: points)
                XCTAssertEqual(Drawn.name(of: ShapeRecognizer.recognize(points, unit: 1)), kind,
                               "\(item.name): recognize is as_line then as_shape")
                results[kind, default: 0] += 1
            }
            XCTAssertGreaterThanOrEqual(results[item.expected] ?? 0, item.atLeast, "\(item.name): \(results)")
            XCTAssertTrue(Set(results.keys).isSubset(of: [item.expected, "ink"]),
                          "\(item.name) became the wrong shape: \(results)")
        }
    }

    func testShapesKeepTheirDrawnSize() {
        let drawn = Drawn.sample(Drawn.points([(100, 100), (260, 100), (260, 200), (100, 200), (100, 104)]))
        guard case let .rectangle(rect)? = ShapeRecognizer.asShape(drawn, unit: 1) else {
            return XCTFail("The rectangle was not recognised")
        }
        XCTAssertEqual(rect.minX, 100, accuracy: 1e-6)
        XCTAssertEqual(rect.minY, 100, accuracy: 1e-6)
        XCTAssertEqual(rect.width, 160, accuracy: 1e-6)
        XCTAssertEqual(rect.height, 100, accuracy: 1e-6)

        let triangle = Drawn.sample(Drawn.points([(100, 300), (220, 300), (160, 200), (101, 299)]))
        guard case let .polygon(corners)? = ShapeRecognizer.asShape(triangle, unit: 1) else {
            return XCTFail("The triangle was not recognised")
        }
        XCTAssertEqual(corners.count, 3)

        // The strokes built for a box trace that box, not a grown or shrunk one.
        let original = TestSupport.stroke(drawn)
        for shape in [RecognizedShape.rectangle(rect), .ellipse(rect)] {
            let built = ShapeStrokeBuilder.strokes(for: shape, replacing: original)
            XCTAssertEqual(built.count, 1)
            guard let stroke = built.first else { continue }
            let box = Drawn.bounds(stroke.path.map { $0.location })
            XCTAssertEqual(box.minX, rect.minX, accuracy: 0.01, "\(shape)")
            XCTAssertEqual(box.minY, rect.minY, accuracy: 0.01, "\(shape)")
            XCTAssertEqual(box.maxX, rect.maxX, accuracy: 0.01, "\(shape)")
            XCTAssertEqual(box.maxY, rect.maxY, accuracy: 0.01, "\(shape)")
        }
    }

    @MainActor func testLineNeedsZoomAwareLength() {
        let ends = ShapeRecognizer.asLine(Drawn.points([(0, 0), (10, 0.5), (40, 0)]), unit: 1)
        XCTAssertEqual(ends?.0, CGPoint(x: 0, y: 0))
        XCTAssertEqual(ends?.1, CGPoint(x: 40, y: 0))
        let short = Drawn.points([(0, 0), (10, 0)])
        XCTAssertNil(ShapeRecognizer.asLine(short, unit: 1), "too short at this zoom")
        XCTAssertNotNil(ShapeRecognizer.asLine(short, unit: 0.25), "zoomed in, 40 px on screen")
        XCTAssertEqual(ShapeRecognizer.recognize(short, unit: 0.25), .line(CGPoint(x: 0, y: 0), CGPoint(x: 10, y: 0)))

        // Through the filter: a 20 pt stroke is a line only when the page is zoomed in.
        let shapes = SmartShapes()
        shapes.isEnabled = true
        let stroke = Drawn.timedStroke(Drawn.sample(Drawn.points([(50, 50), (70, 50)])), step: 0.05)
        let drawing = PKDrawing(strokes: [stroke])
        XCTAssertNil(shapes.filter(pageID: UUID(), old: PKDrawing(), new: drawing, zoom: 1))
        guard let zoomed = shapes.filter(pageID: UUID(), old: PKDrawing(), new: drawing, zoom: 4),
              let line = zoomed.strokes.first else {
            return XCTFail("No line at 4x")
        }
        let locations = line.path.map { $0.location }
        XCTAssertEqual(zoomed.strokes.count, 1)
        XCTAssertEqual(locations.first?.y ?? 0, 50, accuracy: 0.5)
        XCTAssertTrue(locations.allSatisfy { abs($0.y - (locations.first?.y ?? 0)) < 1e-6 }, "the line is straight")
    }

    // MARK: Builder

    func testBuilderPassesThroughCorners() {
        let original = TestSupport.stroke(Drawn.sample(Drawn.points([(100, 100), (260, 100), (260, 200), (100, 200)])))
        let rect = CGRect(x: 100, y: 100, width: 160, height: 100)
        let corners = Drawn.points([(100, 100), (260, 100), (260, 200), (100, 200)])
        let triangle = Drawn.points([(100, 300), (220, 300), (160, 200)])
        let cases: [(RecognizedShape, [CGPoint])] = [
            (.rectangle(rect), corners),
            (.polygon(triangle), triangle),
            (.line(CGPoint(x: 10, y: 20), CGPoint(x: 110, y: 70)), Drawn.points([(10, 20), (110, 70)])),
        ]
        for (shape, vertices) in cases {
            let built = ShapeStrokeBuilder.strokes(for: shape, replacing: original)
            XCTAssertEqual(built.count, 1, "one stroke per shape")
            guard let stroke = built.first, let start = vertices.first, let end = vertices.last else { continue }
            let locations = stroke.path.map { $0.location }
            XCTAssertGreaterThan(locations.count, 6)
            guard locations.count >= 3 else { continue }
            XCTAssertEqual(Array(locations.prefix(3)), [start, start, start], "\(shape)")
            let closed: Bool
            if case .line = shape { closed = false } else { closed = true }
            let finish = closed ? start : end
            XCTAssertEqual(Array(locations.suffix(3)), [finish, finish, finish], "\(shape) ends where it should")
            for corner in vertices {
                let tripled = (0..<(locations.count - 2)).contains { i in
                    locations[i] == corner && locations[i + 1] == corner && locations[i + 2] == corner
                }
                XCTAssertTrue(tripled, "\(corner) is three control points in a row")
            }
            for (p, q) in zip(locations, locations.dropFirst()) {
                XCTAssertLessThanOrEqual(PolylineMath.distance(p, q), 2.0001, "sampled every 2 pt")
            }
            // The B-spline PencilKit draws passes through every corner.
            let drawn = stroke.path.interpolatedPoints(by: .distance(0.5)).map { $0.location }
            for corner in vertices {
                let nearest = drawn.map { PolylineMath.distance($0, corner) }.min() ?? .greatestFiniteMagnitude
                XCTAssertLessThan(nearest, 0.5, "the drawn curve reaches \(corner)")
            }
        }

        // An ellipse: 96 segments, closed.
        guard let ellipse = ShapeStrokeBuilder.strokes(for: .ellipse(rect), replacing: original).first else {
            return XCTFail("No ellipse stroke")
        }
        let locations = ellipse.path.map { $0.location }
        XCTAssertEqual(locations.count, 96 + 1 + 4, "96 segments, the start tripled at both ends")
        XCTAssertEqual(locations.first, locations.last)
    }

    func testBuilderKeepsInkAndDate() {
        let date = Date(timeIntervalSince1970: 1_700_000_000)
        let points = Drawn.sample(Drawn.points([(100, 100), (260, 100), (260, 200), (100, 200), (100, 104)]))
        let widths = points.indices.map { CGFloat($0 % 2 == 0 ? 1 : 4) }
        let mean = widths.reduce(CGFloat(0), +) / CGFloat(widths.count)
        let shapes: [RecognizedShape] = [
            .line(CGPoint(x: 0, y: 0), CGPoint(x: 100, y: 0)),
            .rectangle(CGRect(x: 100, y: 100, width: 160, height: 100)),
            .ellipse(CGRect(x: 100, y: 100, width: 160, height: 100)),
            .polygon(Drawn.points([(100, 300), (220, 300), (160, 200)])),
        ]
        let variants: [(CGFloat, CGAffineTransform)] = [(1, .identity), (2, CGAffineTransform(scaleX: 2, y: 2))]
        for (scale, transform) in variants {
            let original = Drawn.timedStroke(points, step: 0.01, widths: widths, ink: .pencil, color: .systemRed,
                                             date: date, transform: transform)
            let duration = (original.path.last?.timeOffset ?? 0) - (original.path.first?.timeOffset ?? 0)
            for shape in shapes {
                let built = ShapeStrokeBuilder.strokes(for: shape, replacing: original)
                XCTAssertEqual(built.count, 1)
                guard let stroke = built.first else { continue }
                XCTAssertEqual(stroke.ink.inkType, .pencil, "\(shape)")
                XCTAssertTrue(RGB(stroke.ink.color).isClose(to: RGB(original.ink.color)), "\(shape)")
                XCTAssertEqual(stroke.path.creationDate, date, "\(shape)")
                XCTAssertEqual(stroke.transform, .identity)
                XCTAssertNil(stroke.mask)
                for point in stroke.path {
                    XCTAssertEqual(point.size.width, mean * scale, accuracy: 1e-6, "\(shape) mean width")
                }
                XCTAssertEqual(stroke.path.first?.timeOffset ?? -1, 0, accuracy: 1e-9)
                XCTAssertEqual(stroke.path.last?.timeOffset ?? -1, duration, accuracy: 1e-6, "same duration")
            }
        }
    }

    // MARK: Filter

    @MainActor func testFilterIgnoresMarkerAndDisabled() {
        let shapes = SmartShapes()
        let points = Drawn.sample(Drawn.points([(100, 100), (260, 100), (260, 200), (100, 200), (100, 104)]))
        let pen = TestSupport.stroke(points)

        shapes.isEnabled = false
        XCTAssertFalse(UserDefaults.standard.bool(forKey: SettingKey.smartShapes))
        XCTAssertNil(shapes.filter(pageID: UUID(), old: PKDrawing(), new: PKDrawing(strokes: [pen]), zoom: 1))

        shapes.isEnabled = true
        XCTAssertTrue(UserDefaults.standard.bool(forKey: SettingKey.smartShapes), "the setting is the switch")
        XCTAssertTrue(SmartShapes().isEnabled)
        XCTAssertNotNil(shapes.filter(pageID: UUID(), old: PKDrawing(), new: PKDrawing(strokes: [pen]), zoom: 1),
                        "the same stroke with a pen becomes a rectangle")

        let marker = TestSupport.stroke(points, ink: .marker)
        XCTAssertNil(shapes.filter(pageID: UUID(), old: PKDrawing(), new: PKDrawing(strokes: [marker]), zoom: 1))

        let quick = Drawn.timedStroke(points, step: 0.2 / Double(points.count))
        XCTAssertNil(shapes.filter(pageID: UUID(), old: PKDrawing(), new: PKDrawing(strokes: [quick]), zoom: 1),
                     "under 0.25 s")
    }

    @MainActor func testFilterReplacesOnlyLastStroke() {
        let shapes = SmartShapes()
        shapes.isEnabled = true
        let first = TestSupport.stroke((0..<200).map { i in
            CGPoint(x: 100 + 40 * sin(Double(i) / 3) + Double(i), y: 400 + 30 * cos(Double(i) / 5))
        }, date: Date(timeIntervalSince1970: 1))
        let second = TestSupport.stroke(Drawn.sample(Drawn.points([(50, 600), (80, 630), (160, 540)])),
                                        date: Date(timeIntervalSince1970: 2))
        let drawnDate = Date(timeIntervalSince1970: 3)
        let rectangle = TestSupport.stroke(
            Drawn.sample(Drawn.points([(100, 100), (260, 100), (260, 200), (100, 200), (100, 104)])),
            color: .systemBlue, date: drawnDate)
        let old = PKDrawing(strokes: [first, second])

        guard let result = shapes.filter(pageID: UUID(), old: old, new: PKDrawing(strokes: [first, second, rectangle]),
                                         zoom: 1) else {
            return XCTFail("The rectangle was not replaced")
        }
        XCTAssertEqual(result.strokes.count, 3)
        guard result.strokes.count == 3 else { return }
        for (kept, before) in zip(result.strokes.prefix(2), [first, second]) {
            XCTAssertEqual(kept.path.creationDate, before.path.creationDate)
            XCTAssertEqual(kept.path.count, before.path.count)
            XCTAssertEqual(kept.path.first?.location, before.path.first?.location)
        }
        let shape = result.strokes[2]
        XCTAssertEqual(shape.path.creationDate, drawnDate)
        XCTAssertTrue(RGB(shape.ink.color).isClose(to: RGB(UIColor.systemBlue)))
        let locations = shape.path.map { $0.location }
        XCTAssertEqual(locations.prefix(3).filter { $0 == locations.first }.count, 3, "starts at a tripled corner")
        XCTAssertEqual(locations.first, locations.last, "closed")
        let box = Drawn.bounds(locations)
        XCTAssertEqual(box.minX, 100, accuracy: 1.5)
        XCTAssertEqual(box.minY, 100, accuracy: 1.5)
        XCTAssertEqual(box.maxX, 260, accuracy: 1.5)
        XCTAssertEqual(box.maxY, 200, accuracy: 1.5)

        // A last stroke that is ink stays ink.
        let scribble = TestSupport.stroke((0..<200).map { i in
            CGPoint(x: 300 + 40 * sin(Double(i) / 3) + Double(i), y: 100 + 30 * cos(Double(i) / 5))
        }, date: Date(timeIntervalSince1970: 4))
        XCTAssertNil(shapes.filter(pageID: UUID(), old: old, new: PKDrawing(strokes: [first, second, scribble]), zoom: 1))
    }

    @MainActor func testFilterNeedsExactlyOneNewStroke() {
        let shapes = SmartShapes()
        shapes.isEnabled = true
        let first = TestSupport.stroke(Drawn.sample(Drawn.points([(50, 600), (80, 630), (160, 540)])),
                                       date: Date(timeIntervalSince1970: 1))
        let rectangle = TestSupport.stroke(
            Drawn.sample(Drawn.points([(100, 100), (260, 100), (260, 200), (100, 200), (100, 104)])),
            date: Date(timeIntervalSince1970: 2))
        let triangle = TestSupport.stroke(
            Drawn.sample(Drawn.points([(100, 300), (220, 300), (160, 200), (103, 296)])),
            date: Date(timeIntervalSince1970: 3))
        let id = UUID()
        let old = PKDrawing(strokes: [first])

        XCTAssertNotNil(shapes.filter(pageID: id, old: old, new: PKDrawing(strokes: [first, rectangle]), zoom: 1),
                        "one stroke added at the end")
        XCTAssertNil(shapes.filter(pageID: id, old: old, new: old, zoom: 1), "nothing added")
        XCTAssertNil(shapes.filter(pageID: id, old: old, new: PKDrawing(strokes: [first, rectangle, triangle]), zoom: 1),
                     "two strokes added")
        XCTAssertNil(shapes.filter(pageID: id, old: PKDrawing(strokes: [first, rectangle]),
                                   new: PKDrawing(strokes: [rectangle]), zoom: 1), "a stroke removed")
        let changed = TestSupport.stroke(Drawn.sample(Drawn.points([(50, 600), (80, 630), (160, 540)])),
                                         date: Date(timeIntervalSince1970: 9))
        XCTAssertNil(shapes.filter(pageID: id, old: old, new: PKDrawing(strokes: [changed, rectangle]), zoom: 1),
                     "an earlier stroke changed as well")
        XCTAssertNil(shapes.filter(pageID: id, old: old, new: PKDrawing(strokes: [rectangle, first]), zoom: 1),
                     "the new stroke is not at the end")
    }
}

/// A seeded generator, so the jittered cases are the same on every run: SplitMix64 and Box-Muller.
private struct ShapeRandom {
    private var state: UInt64

    init(seed: UInt64) { state = seed }

    mutating func next() -> UInt64 {
        state = state &+ 0x9E37_79B9_7F4A_7C15
        var z = state
        z = (z ^ (z >> 30)) &* 0xBF58_476D_1CE4_E5B9
        z = (z ^ (z >> 27)) &* 0x94D0_49BB_1331_11EB
        return z ^ (z >> 31)
    }

    mutating func uniform() -> Double {
        Double(next() >> 11) * 0x1.0p-53
    }

    mutating func gauss(_ sigma: Double) -> Double {
        let u1 = 1 - uniform()
        let u2 = uniform()
        return sigma * (-2 * log(u1)).squareRoot() * cos(2 * Double.pi * u2)
    }
}

private struct DrawnCase {
    let name: String
    let make: () -> [CGPoint]
    let expected: String
    let atLeast: Int
}

/// The desktop's tests/test_inking.py helpers and CASES, in the same order.
private enum Drawn {
    static func points(_ pairs: [(Double, Double)]) -> [CGPoint] {
        pairs.map { CGPoint(x: $0.0, y: $0.1) }
    }

    static func sample(_ polyline: [CGPoint], step: CGFloat = 1.5) -> [CGPoint] {
        guard let last = polyline.last else { return [] }
        var out: [CGPoint] = []
        for (p, q) in zip(polyline, polyline.dropFirst()) {
            let n = max(1, Int(PolylineMath.distance(p, q) / step))
            for i in 0..<n {
                out.append(CGPoint(x: p.x + (q.x - p.x) * CGFloat(i) / CGFloat(n),
                                   y: p.y + (q.y - p.y) * CGFloat(i) / CGFloat(n)))
            }
        }
        out.append(last)
        return out
    }

    static func ellipse(_ cx: Double, _ cy: Double, _ a: Double, _ b: Double, n: Int = 120, start: Double = 0.3,
                        overlap: Double = 0.05) -> [CGPoint] {
        (0...n).map { i in
            let t = 2 * Double.pi * (1 + overlap) * Double(i) / Double(n)
            return CGPoint(x: cx + a * cos(start + t), y: cy + b * sin(start + t))
        }
    }

    /// Hand tremor: noise that drifts smoothly, a moving average of white noise.
    static func jitter(_ points: [CGPoint], amount: Double, rng: inout ShapeRandom) -> [CGPoint] {
        var raw: [(Double, Double)] = []
        for _ in points {
            let x = rng.gauss(amount * 2.5)
            let y = rng.gauss(amount * 2.5)
            raw.append((x, y))
        }
        var out: [CGPoint] = []
        for i in points.indices {
            let low = max(0, i - 4), high = min(raw.count, i + 5)
            var sx = 0.0, sy = 0.0
            for k in low..<high {
                sx += raw[k].0
                sy += raw[k].1
            }
            let count = Double(high - low)
            out.append(CGPoint(x: points[i].x + CGFloat(sx / count), y: points[i].y + CGFloat(sy / count)))
        }
        return out
    }

    static func cases() -> [DrawnCase] {
        let pentagon = (0..<6).map { k -> CGPoint in
            let angle = (-90 + 72 * Double(k)) * (Double.pi / 180)
            return CGPoint(x: 200 + 80 * cos(angle), y: 200 + 80 * sin(angle))
        }
        return [
            DrawnCase(name: "rectangle", make: {
                Drawn.sample(Drawn.points([(100, 100), (260, 102), (258, 200), (101, 198), (104, 106)]))
            }, expected: "rectangle", atLeast: 40),
            DrawnCase(name: "ellipse", make: { Drawn.ellipse(200, 200, 90, 50) }, expected: "ellipse", atLeast: 40),
            DrawnCase(name: "circle", make: { Drawn.ellipse(200, 200, 60, 60) }, expected: "ellipse", atLeast: 40),
            DrawnCase(name: "triangle", make: {
                Drawn.sample(Drawn.points([(100, 300), (220, 300), (160, 200), (103, 296)]))
            }, expected: "polygon", atLeast: 40),
            DrawnCase(name: "pentagon", make: { Drawn.sample(pentagon) }, expected: "polygon", atLeast: 25),
            DrawnCase(name: "rotated square", make: {
                Drawn.sample(Drawn.points([(200, 100), (300, 200), (200, 300), (100, 200), (198, 103)]))
            }, expected: "polygon", atLeast: 40),
            DrawnCase(name: "line", make: { Drawn.sample(Drawn.points([(50, 50), (400, 80)])) }, expected: "line", atLeast: 40),
            DrawnCase(name: "scribble", make: {
                (0..<200).map { i in
                    CGPoint(x: 100 + 40 * sin(Double(i) / 3) + Double(i), y: 100 + 30 * cos(Double(i) / 5))
                }
            }, expected: "ink", atLeast: 40),
            DrawnCase(name: "slight curve", make: {
                (0..<101).map { i in
                    let d = Double(i * 3 - 150)
                    return CGPoint(x: 50 + Double(i * 3), y: 100 + 0.0009 * (d * d))
                }
            }, expected: "ink", atLeast: 40),
            DrawnCase(name: "zigzag back", make: { Drawn.sample(Drawn.points([(50, 50), (300, 52), (150, 51)])) },
                      expected: "ink", atLeast: 40),
            DrawnCase(name: "check mark", make: { Drawn.sample(Drawn.points([(50, 100), (80, 130), (160, 40)])) },
                      expected: "ink", atLeast: 40),
            DrawnCase(name: "open arc", make: { Drawn.ellipse(200, 200, 90, 50, start: 0, overlap: -0.4) },
                      expected: "ink", atLeast: 40),
        ]
    }

    /// As the desktop test classifies: "line" when as_line accepts, else as_shape's kind, else "ink".
    static func kind(of points: [CGPoint]) -> String {
        if ShapeRecognizer.asLine(points, unit: 1) != nil { return "line" }
        return name(of: ShapeRecognizer.asShape(points, unit: 1))
    }

    static func name(of shape: RecognizedShape?) -> String {
        guard let shape else { return "ink" }
        switch shape {
        case .line: return "line"
        case .rectangle: return "rectangle"
        case .ellipse: return "ellipse"
        case .polygon: return "polygon"
        }
    }

    static func bounds(_ points: [CGPoint]) -> CGRect {
        guard let first = points.first else { return .null }
        var minX = first.x, maxX = first.x, minY = first.y, maxY = first.y
        for p in points {
            minX = min(minX, p.x)
            maxX = max(maxX, p.x)
            minY = min(minY, p.y)
            maxY = max(maxY, p.y)
        }
        return CGRect(x: minX, y: minY, width: maxX - minX, height: maxY - minY)
    }

    /// A stroke through `points` as control points, one every `step` seconds.
    static func timedStroke(_ points: [CGPoint], step: TimeInterval, widths: [CGFloat]? = nil,
                            ink: PKInkingTool.InkType = .pen, color: UIColor = .black, date: Date = Date(),
                            transform: CGAffineTransform = .identity) -> PKStroke {
        var controlPoints: [PKStrokePoint] = []
        for (index, location) in points.enumerated() {
            let width = widths.map { index < $0.count ? $0[index] : 2 } ?? 2
            controlPoints.append(PKStrokePoint(location: location, timeOffset: TimeInterval(index) * step,
                                               size: CGSize(width: width, height: width), opacity: 1, force: 1,
                                               azimuth: 0, altitude: .pi / 2))
        }
        let path = PKStrokePath(controlPoints: controlPoints, creationDate: date)
        return PKStroke(ink: PKInk(ink, color: color), path: path, transform: transform, mask: nil)
    }
}
