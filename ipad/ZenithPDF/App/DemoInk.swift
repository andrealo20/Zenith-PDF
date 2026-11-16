import CoreText
import Foundation
import PencilKit
import UIKit

/// Handwriting-like strokes from glyph outlines, and their word rects.
enum DemoInk {
    /// A handwriting face that ships with iPadOS, named so CoreText needs no UIFont bridging. Its thin stems make
    /// the two sides of each outline run close together, so the letters read as written with one pen line.
    private static let fontName = "Noteworthy-Light" as CFString
    /// Forward lean, as x offset per point of height above the baseline.
    private static let slant: CGFloat = 0.12

    /// Handwriting-like strokes: each glyph outline of `text` in a handwriting face, flattened and sampled.
    /// `origin` is the top-left corner of the line, display space; one stroke per outline, dated one after another.
    static func strokes(_ text: String, at origin: CGPoint, size: CGFloat, ink: PKInk, startingAt date: Date, secondsPerStroke: TimeInterval) -> [PKStroke] {
        let layout = Layout(text: text, size: size)
        let width = max(1.2, size * 0.035)
        let spacing = max(0.8, size / 50)
        var strokes: [PKStroke] = []
        for (index, glyph) in layout.glyphs.enumerated() {
            guard let path = CTFontCreatePathForGlyph(layout.font, glyph, nil) else { continue }
            let pen = layout.offsets[index]
            for contour in contours(of: path) {
                let points = resample(contour.map { layout.display($0, pen: pen, origin: origin) }, spacing: spacing)
                guard points.count > 1 else { continue }
                let created = date.addingTimeInterval(Double(strokes.count) * secondsPerStroke)
                strokes.append(stroke(points, width: width, ink: ink, date: created))
            }
        }
        return strokes
    }

    /// One rect per word of `text`, display space, matching what strokes(_:at:size:...) draws.
    static func words(_ text: String, at origin: CGPoint, size: CGFloat) -> [InkWord] {
        let layout = Layout(text: text, size: size)
        let characters = Array(text.utf16)
        var words: [InkWord] = []
        var start: Int? = nil
        for index in 0...characters.count {
            let isSpace = index == characters.count || characters[index] == 32
            if !isSpace, start == nil { start = index }
            guard isSpace, let first = start else { continue }
            let left = layout.offsets[first]
            let right = index < layout.offsets.count ? layout.offsets[index] : layout.width
            let wordText = String(decoding: characters[first..<index], as: UTF16.self)
            let rect = CGRect(x: origin.x + left, y: origin.y,
                              width: right - left + slant * layout.ascent, height: layout.ascent + layout.descent)
            words.append(InkWord(text: wordText, rect: rect))
            start = nil
        }
        return words
    }

    /// A straight pen line from `from` to `to`, sampled every 2 points with its ends tripled so the B-spline
    /// reaches them.
    static func underline(from: CGPoint, to: CGPoint, ink: PKInk, date: Date) -> PKStroke {
        let length = PolylineMath.distance(from, to)
        let steps = max(Int(length / 2), 1)
        var points = (0...steps).map { step -> CGPoint in
            let t = CGFloat(step) / CGFloat(steps)
            return CGPoint(x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t)
        }
        points = [from, from] + points + [to, to]
        return stroke(points, width: 2, ink: ink, date: date)
    }

    /// Width of `text` as strokes(_:...) lays it out, slant included.
    static func width(_ text: String, size: CGFloat) -> CGFloat {
        let layout = Layout(text: text, size: size)
        return layout.width + slant * layout.ascent
    }

    /// Height from the top of a line to its baseline, so callers can sit the writing on a rule.
    static func ascent(size: CGFloat) -> CGFloat {
        Layout(text: "", size: size).ascent
    }

    // MARK: - Private

    /// Glyphs of the text, one per UTF-16 unit, with the pen position before each.
    private struct Layout {
        let font: CTFont
        var glyphs: [CGGlyph]
        var offsets: [CGFloat]
        var width: CGFloat
        let ascent: CGFloat
        let descent: CGFloat

        init(text: String, size: CGFloat) {
            font = CTFontCreateWithName(DemoInk.fontName, size, nil)
            let characters = Array(text.utf16)
            var glyphs = [CGGlyph](repeating: 0, count: characters.count)
            if !characters.isEmpty { _ = CTFontGetGlyphsForCharacters(font, characters, &glyphs, characters.count) }
            var advances = [CGSize](repeating: .zero, count: characters.count)
            if !characters.isEmpty {
                _ = CTFontGetAdvancesForGlyphs(font, .horizontal, glyphs, &advances, characters.count)
            }
            var offsets: [CGFloat] = []
            var pen: CGFloat = 0
            for advance in advances {
                offsets.append(pen)
                pen += advance.width
            }
            self.glyphs = glyphs
            self.offsets = offsets
            width = pen
            ascent = CTFontGetAscent(font)
            descent = CTFontGetDescent(font)
        }

        /// Glyph space (y up from the baseline) to display space (y down), slanted forward.
        func display(_ point: CGPoint, pen: CGFloat, origin: CGPoint) -> CGPoint {
            CGPoint(x: origin.x + pen + point.x + DemoInk.slant * point.y, y: origin.y + ascent - point.y)
        }
    }

    private static func stroke(_ points: [CGPoint], width: CGFloat, ink: PKInk, date: Date) -> PKStroke {
        let controlPoints = points.enumerated().map { item in
            PKStrokePoint(location: item.element, timeOffset: TimeInterval(item.offset) * 0.005,
                          size: CGSize(width: width, height: width), opacity: 1, force: 1, azimuth: 0, altitude: .pi / 2)
        }
        return PKStroke(ink: ink, path: PKStrokePath(controlPoints: controlPoints, creationDate: date),
                        transform: .identity, mask: nil)
    }

    /// Closed outlines of a glyph path, curves flattened.
    private static func contours(of path: CGPath) -> [[CGPoint]] {
        var result: [[CGPoint]] = []
        var current: [CGPoint] = []
        var start = CGPoint.zero
        path.applyWithBlock { element in
            let points = element.pointee.points
            let last = current.last ?? start
            switch element.pointee.type {
            case .moveToPoint:
                if current.count > 1 { result.append(current) }
                start = points[0]
                current = [points[0]]
            case .addLineToPoint:
                current.append(points[0])
            case .addQuadCurveToPoint:
                let control = points[0], end = points[1]
                for step in 1...8 {
                    let t = CGFloat(step) / 8, u = 1 - t
                    current.append(CGPoint(x: u * u * last.x + 2 * u * t * control.x + t * t * end.x,
                                           y: u * u * last.y + 2 * u * t * control.y + t * t * end.y))
                }
            case .addCurveToPoint:
                let c1 = points[0], c2 = points[1], end = points[2]
                for step in 1...12 {
                    let t = CGFloat(step) / 12, u = 1 - t
                    let a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t
                    current.append(CGPoint(x: a * last.x + b * c1.x + c * c2.x + d * end.x,
                                           y: a * last.y + b * c1.y + c * c2.y + d * end.y))
                }
            case .closeSubpath:
                if current.count > 1 {
                    current.append(start)
                    result.append(current)
                }
                current = []
            @unknown default:
                break
            }
        }
        if current.count > 1 { result.append(current) }
        return result
    }

    /// Points every `spacing` along the polyline, its ends kept.
    private static func resample(_ points: [CGPoint], spacing: CGFloat) -> [CGPoint] {
        guard var previous = points.first else { return [] }
        var result = [previous]
        var carried: CGFloat = 0
        for point in points.dropFirst() {
            var segment = PolylineMath.distance(previous, point)
            var from = previous
            while carried + segment >= spacing, segment > 0 {
                let t = (spacing - carried) / segment
                let next = CGPoint(x: from.x + (point.x - from.x) * t, y: from.y + (point.y - from.y) * t)
                result.append(next)
                segment -= spacing - carried
                from = next
                carried = 0
            }
            carried += segment
            previous = point
        }
        if let last = points.last, result.last != last { result.append(last) }
        return result
    }
}
