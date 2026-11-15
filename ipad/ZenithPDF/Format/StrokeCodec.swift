import CoreGraphics
import CryptoKit
import Foundation
import PencilKit
import UIKit

/// What the iPad wrote for one stroke, kept in /ZenithInkCheck so a later read can tell what the desktop
/// changed (section 4.3) without interpolating anything. Text form, numbers with 2 decimals:
/// "1;<shape>;<x0> <y0>;<rotation> <box x> <box y> <box w> <box h>;<r> <g> <b>;<opacity>;<0|1>;<width>"
struct InkCheck: Equatable, Sendable {
    var shape: String                 // InkCheck.shapeHash of the InkList as written
    var origin: CGPoint               // first point of the InkList, PDF space
    var rotation: Int                 // page rotation when written
    var box: CGRect                   // page's visible box when written, PDF space
    var color: RGB                    // /C
    var opacity: CGFloat              // /CA
    var highlighter: Bool             // /ZenithHighlighter
    var width: CGFloat                // /BS /W

    init(paths: [[CGPoint]], geometry: PageGeometry, color: RGB, opacity: CGFloat, highlighter: Bool, width: CGFloat) {
        self.shape = InkCheck.shapeHash(paths)
        self.origin = paths.first?.first ?? .zero
        self.rotation = geometry.rotation
        self.box = geometry.box
        self.color = color
        self.opacity = opacity
        self.highlighter = highlighter
        self.width = width
    }

    /// nil for an unknown version or a malformed value.
    init?(string: String) {
        let parts = string.split(separator: ";", omittingEmptySubsequences: false).map(String.init)
        guard parts.count == 8, parts[0] == "1", !parts[1].isEmpty else { return nil }
        guard let origin = InkCheck.numbers(parts[2]), origin.count == 2,
              let page = InkCheck.numbers(parts[3]), page.count == 5,
              let rgb = InkCheck.numbers(parts[4]), rgb.count == 3,
              let opacity = Double(parts[5]), let width = Double(parts[7]), parts[6] == "0" || parts[6] == "1" else {
            return nil
        }
        shape = parts[1]
        self.origin = CGPoint(x: origin[0], y: origin[1])
        rotation = Int(page[0].rounded())
        box = CGRect(x: page[1], y: page[2], width: page[3], height: page[4])
        color = RGB(r: rgb[0], g: rgb[1], b: rgb[2])
        self.opacity = CGFloat(opacity)
        highlighter = parts[6] == "1"
        self.width = CGFloat(width)
    }

    var string: String {
        let f = InkCheck.format
        return ["1", shape, "\(f(origin.x)) \(f(origin.y))",
                "\(rotation) \(f(box.minX)) \(f(box.minY)) \(f(box.width)) \(f(box.height))",
                "\(f(color.r)) \(f(color.g)) \(f(color.b))", f(opacity), highlighter ? "1" : "0", f(width)]
            .joined(separator: ";")
    }

    /// SHA-256 hex of every point minus the first point of the first path, each coordinate rounded to 0.01,
    /// with the path and point counts. Moving the whole stroke leaves it unchanged; any reshaping changes it.
    static func shapeHash(_ paths: [[CGPoint]]) -> String {
        let first = paths.first?.first ?? .zero
        // Whole hundredths, so -0.00 and 0.00 cannot differ.
        let centi = { (v: CGFloat) -> Int in Int((v * 100).rounded()) }
        var text = "\(paths.count)"
        for path in paths {
            text += "|\(path.count):"
            text += path.map { "\(centi($0.x - first.x)),\(centi($0.y - first.y))" }.joined(separator: ";")
        }
        return SHA256.hash(data: Data(text.utf8)).map { String(format: "%02x", $0) }.joined()
    }

    private static func format(_ value: CGFloat) -> String {
        let text = String(format: "%.2f", Double(value))
        return text == "-0.00" ? "0.00" : text
    }

    private static func numbers(_ text: String) -> [CGFloat]? {
        let values = text.split(separator: " ").map { Double($0) }
        guard !values.contains(where: { $0 == nil }) else { return nil }
        return values.compactMap { $0 }.map { CGFloat($0) }
    }
}

/// The data of one /Ink annotation the iPad writes: one PencilKit stroke, in PDF space.
struct InkRecord: Equatable {
    var identifier: String            // /NM
    var paths: [[CGPoint]]            // /InkList: the stroke's visible pieces, every coordinate on the 0.01 grid
    var widths: [[CGFloat]]           // /ZenithWidths, one per point, rounded to 0.01
    var color: RGB                    // /C
    var opacity: CGFloat              // /CA
    var highlighter: Bool             // /ZenithHighlighter
    var pencilKit: String             // base64 of PKDrawing(strokes: [the stroke]).dataRepresentation()
    var check: InkCheck               // /ZenithInkCheck

    /// /BS /W
    var maxWidth: CGFloat { widths.joined().max() ?? 0 }

    /// Union of points grown by maxWidth / 2 + 1, rounded outward to the 0.01 grid.
    var bounds: CGRect {
        let points = paths.joined()
        guard let first = points.first else { return .zero }
        var minX = first.x, maxX = first.x, minY = first.y, maxY = first.y
        for p in points {
            minX = min(minX, p.x); maxX = max(maxX, p.x)
            minY = min(minY, p.y); maxY = max(maxY, p.y)
        }
        let grow = maxWidth / 2 + 1
        let left = ((minX - grow) * 100).rounded(.down) / 100
        let bottom = ((minY - grow) * 100).rounded(.down) / 100
        let right = ((maxX + grow) * 100).rounded(.up) / 100
        let top = ((maxY + grow) * 100).rounded(.up) / 100
        return CGRect(x: left, y: bottom, width: right - left, height: top - bottom)
    }
}

enum StrokeCodec {
    static let sampleSpacing: CGFloat = 1.5        // points between interpolated samples, display space
    static let simplifyTolerance: CGFloat = 0.15   // Ramer-Douglas-Peucker tolerance, display space
    static let originTolerance: CGFloat = 0.05     // PDF space: a first point within this has not moved
    static let styleTolerance: CGFloat = 0.011     // colour channels, opacity and width within this are unchanged

    /// One record per stroke, in drawing order (section 4.2). A stroke with no visible piece writes nothing.
    static func records(for drawing: PKDrawing, geometry: PageGeometry) -> [InkRecord] {
        var used: [String: Int] = [:]
        var result: [InkRecord] = []
        for stroke in drawing.strokes {
            guard var written = record(for: stroke, geometry: geometry) else { continue }
            // Two identical strokes (a pasted copy in place) would share an /NM, which must be unique.
            let seen = used[written.identifier, default: 0]
            used[written.identifier] = seen + 1
            if seen > 0 { written.identifier += "-\(seen + 1)" }
            result.append(written)
        }
        return result
    }

    static func record(for stroke: PKStroke, geometry: PageGeometry) -> InkRecord? {
        var paths: [[CGPoint]] = []
        var widths: [[CGFloat]] = []
        for piece in polylines(of: stroke) where !piece.points.isEmpty {
            paths.append(piece.points.map { round2(geometry.toPDF($0)) })
            widths.append(piece.widths.map { round2($0) })
        }
        guard !paths.isEmpty else { return nil }
        let look = style(of: stroke.ink)
        let encoded = PKDrawing(strokes: [stroke]).dataRepresentation().base64EncodedString()
        let widest = widths.joined().max() ?? 0
        let check = InkCheck(paths: paths, geometry: geometry, color: look.color, opacity: look.opacity,
                             highlighter: look.highlighter, width: widest)
        return InkRecord(identifier: identifier(for: stroke), paths: paths, widths: widths, color: look.color,
                         opacity: look.opacity, highlighter: look.highlighter, pencilKit: encoded, check: check)
    }

    /// Visible pieces of one stroke in display space with a width per point (masks honoured).
    static func polylines(of stroke: PKStroke) -> [(points: [CGPoint], widths: [CGFloat])] {
        let path = stroke.path
        guard path.count > 0 else { return [] }
        let ranges: [ClosedRange<CGFloat>] = stroke.mask == nil
            ? [0...CGFloat(path.count - 1)]
            : stroke.maskedPathRanges
        let t = stroke.transform
        let scale = max(sqrt(abs(t.a * t.d - t.b * t.c)), 0)
        var result: [(points: [CGPoint], widths: [CGFloat])] = []
        for range in ranges {
            var points: [CGPoint] = []
            var widths: [CGFloat] = []
            for value in parameters(of: path, in: range) {
                let point = path.interpolatedPoint(at: value)
                points.append(point.location.applying(t))
                widths.append(max(point.size.width * scale, 0.1))
            }
            guard let first = points.first, let firstWidth = widths.first else { continue }
            if points.count == 1 || points.allSatisfy({ $0 == first }) {
                // A dot: two points, as the desktop's appearance draws one.
                result.append((points: [first, CGPoint(x: first.x + 0.01, y: first.y)], widths: [firstWidth, firstWidth]))
                continue
            }
            let kept = PolylineMath.simplifyIndices(points, tolerance: simplifyTolerance)
            result.append((points: kept.map { points[$0] }, widths: kept.map { widths[$0] }))
        }
        return result
    }

    /// Parametric values every sampleSpacing along the curve, plus every whole value (control point) inside the
    /// range, so a stroke rebuilt from a polyline (tripled control points) keeps its corners exactly.
    private static func parameters(of path: PKStrokePath, in range: ClosedRange<CGFloat>) -> [CGFloat] {
        let end = min(range.upperBound, CGFloat(path.count - 1))
        var current = max(range.lowerBound, 0)
        guard current <= end else { return [] }
        var values: [CGFloat] = [current]
        // A path of n control points spaced at least 0.01 apart needs far fewer steps; the bound only stops a loop.
        var guardCount = 0
        while current < end && guardCount < 1_000_000 {
            guardCount += 1
            var next = path.parametricValue(current, offsetBy: .distance(sampleSpacing))
            if !(next > current + 1e-6) || next > end { next = end }
            var whole = (current + 1e-6).rounded(.up)
            while whole < next - 1e-6 {
                values.append(whole)
                whole += 1
            }
            values.append(next)
            current = next
        }
        return values
    }

    /// The strokes one /Ink annotation reads as: stored, moved, restyled or rebuilt (section 4.3).
    static func strokes(from raw: RawAnnotation, geometry: PageGeometry) -> [PKStroke] {
        let paths = (raw.inkList ?? []).filter { !$0.isEmpty }
        guard !paths.isEmpty else { return [] }
        let color = raw.rgb ?? .black
        let opacity = raw.opacity ?? 1
        let width = raw.borderWidth ?? 1
        let rebuilt = {
            rebuild(paths: paths, widths: raw.widths, width: width, color: color, opacity: opacity,
                    highlighter: raw.highlighter, geometry: geometry)
        }
        guard let checkText = raw.inkCheck, let check = InkCheck(string: checkText),
              let encoded = raw.pencilKit, let data = Data(base64Encoded: encoded),
              let stored = try? PKDrawing(data: data), !stored.strokes.isEmpty else { return rebuilt() }
        guard check.rotation == geometry.rotation, close(check.box, geometry.box) else { return rebuilt() }
        guard InkCheck.shapeHash(paths) == check.shape else { return rebuilt() }

        var strokes = stored.strokes
        let first = paths[0][0]
        let dx = first.x - check.origin.x, dy = first.y - check.origin.y
        if hypot(dx, dy) > originTolerance {
            let a = geometry.toDisplay(first), b = geometry.toDisplay(check.origin)
            let move = CGAffineTransform(translationX: a.x - b.x, y: a.y - b.y)
            strokes = strokes.map {
                PKStroke(ink: $0.ink, path: $0.path, transform: $0.transform.concatenating(move), mask: $0.mask,
                         randomSeed: $0.randomSeed)
            }
        }
        let restyled = !color.isClose(to: check.color, tolerance: styleTolerance)
            || abs(opacity - check.opacity) > styleTolerance
            || abs(width - check.width) > styleTolerance
            || raw.highlighter != check.highlighter
        if restyled {
            let scale = check.width > 0 ? width / check.width : 1
            strokes = restyle(strokes, color: color, opacity: opacity, highlighter: raw.highlighter, widthScale: scale)
        }
        return strokes
    }

    /// Ink with no usable stored stroke: one stroke per path, every point tripled as a control point, so the
    /// uniform cubic B-spline of PKStrokePath runs along the polyline itself [R9].
    static func rebuild(paths: [[CGPoint]], widths: [[CGFloat]]?, width: CGFloat, color: RGB, opacity: CGFloat,
                        highlighter: Bool, geometry: PageGeometry) -> [PKStroke] {
        // Widths count only when every path has one per point, as the desktop reads them.
        let fits = widths.map { $0.count == paths.count && zip($0, paths).allSatisfy { $0.count == $1.count } } ?? false
        let ink = PKInk(highlighter ? .marker : .pen, color: color.uiColor(alpha: opacity))
        var result: [PKStroke] = []
        for (p, path) in paths.enumerated() where !path.isEmpty {
            var controlPoints: [PKStrokePoint] = []
            for (i, point) in path.enumerated() {
                let w = max(fits ? (widths?[p][i] ?? width) : width, 0.1)
                let control = PKStrokePoint(location: geometry.toDisplay(point), timeOffset: TimeInterval(i) * 0.004,
                                            size: CGSize(width: w, height: w), opacity: 1, force: 1, azimuth: 0,
                                            altitude: .pi / 2)
                controlPoints.append(contentsOf: [control, control, control])
            }
            let strokePath = PKStrokePath(controlPoints: controlPoints, creationDate: .distantPast)
            result.append(PKStroke(ink: ink, path: strokePath, transform: .identity, mask: nil))
        }
        return result
    }

    /// The stored strokes with the colour, opacity and highlighter flag the file now has, and every point's size
    /// multiplied by widthScale. A highlighter becomes .marker; a former marker that is no longer one becomes .pen.
    static func restyle(_ strokes: [PKStroke], color: RGB, opacity: CGFloat, highlighter: Bool, widthScale: CGFloat) -> [PKStroke] {
        let scale = widthScale.isFinite && widthScale > 0 ? widthScale : 1
        return strokes.map { stroke in
            var type = stroke.ink.inkType
            if highlighter {
                type = .marker
            } else if type == .marker {
                type = .pen
            }
            let ink = PKInk(type, color: color.uiColor(alpha: opacity))
            let points = stroke.path.map { p in
                PKStrokePoint(location: p.location, timeOffset: p.timeOffset,
                              size: CGSize(width: p.size.width * scale, height: p.size.height * scale),
                              opacity: p.opacity, force: p.force, azimuth: p.azimuth, altitude: p.altitude,
                              secondaryScale: p.secondaryScale)
            }
            let path = PKStrokePath(controlPoints: points, creationDate: stroke.path.creationDate)
            return PKStroke(ink: ink, path: path, transform: stroke.transform, mask: stroke.mask,
                            randomSeed: stroke.randomSeed)
        }
    }

    /// "zenith-" + UUID from SHA-256 of creation date, point count, first point.
    static func identifier(for stroke: PKStroke) -> String {
        let path = stroke.path
        let first = path.first.map { $0.location.applying(stroke.transform) } ?? .zero
        let text = String(format: "%.6f|%ld|%.3f|%.3f", path.creationDate.timeIntervalSinceReferenceDate,
                          path.count, Double(first.x), Double(first.y))
        let bytes = Array(SHA256.hash(data: Data(text.utf8)))
        let hex = bytes.prefix(16).map { String(format: "%02x", $0) }.joined()
        let chars = Array(hex)
        let groups = [0..<8, 8..<12, 12..<16, 16..<20, 20..<32].map { String(chars[$0]) }
        return "zenith-" + groups.joined(separator: "-")
    }

    static func style(of ink: PKInk) -> (color: RGB, opacity: CGFloat, highlighter: Bool) {
        let resolved = ink.color.resolvedColor(with: UITraitCollection(userInterfaceStyle: .light))
        let alpha = resolved.cgColor.alpha
        return (color: RGB(resolved), opacity: min(max(alpha, 0), 1), highlighter: ink.inkType == .marker)
    }

    private static func round2(_ value: CGFloat) -> CGFloat {
        (value * 100).rounded() / 100
    }

    private static func round2(_ point: CGPoint) -> CGPoint {
        CGPoint(x: round2(point.x), y: round2(point.y))
    }

    /// Boxes equal to the 2 decimals the check keeps.
    private static func close(_ a: CGRect, _ b: CGRect) -> Bool {
        abs(a.minX - b.minX) <= 0.011 && abs(a.minY - b.minY) <= 0.011
            && abs(a.width - b.width) <= 0.011 && abs(a.height - b.height) <= 0.011
    }
}
