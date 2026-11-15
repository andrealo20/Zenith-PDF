import CoreGraphics
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
        return nil
    }

    var string: String { "" }

    /// SHA-256 hex of every point minus the first point of the first path, each coordinate rounded to 0.01,
    /// with the path and point counts. Moving the whole stroke leaves it unchanged; any reshaping changes it.
    static func shapeHash(_ paths: [[CGPoint]]) -> String {
        ""
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
    var bounds: CGRect { .zero }
}

enum StrokeCodec {
    static let sampleSpacing: CGFloat = 1.5        // points between interpolated samples, display space
    static let simplifyTolerance: CGFloat = 0.15   // Ramer-Douglas-Peucker tolerance, display space
    static let originTolerance: CGFloat = 0.05     // PDF space: a first point within this has not moved
    static let styleTolerance: CGFloat = 0.011     // colour channels, opacity and width within this are unchanged

    /// One record per stroke, in drawing order (section 4.2). A stroke with no visible piece writes nothing.
    static func records(for drawing: PKDrawing, geometry: PageGeometry) -> [InkRecord] {
        []
    }

    static func record(for stroke: PKStroke, geometry: PageGeometry) -> InkRecord? {
        nil
    }

    /// Visible pieces of one stroke in display space with a width per point (masks honoured).
    static func polylines(of stroke: PKStroke) -> [(points: [CGPoint], widths: [CGFloat])] {
        []
    }

    /// The strokes one /Ink annotation reads as: stored, moved, restyled or rebuilt (section 4.3).
    static func strokes(from raw: RawAnnotation, geometry: PageGeometry) -> [PKStroke] {
        []
    }

    /// Ink with no usable stored stroke: one stroke per path, every point tripled as a control point, so the
    /// uniform cubic B-spline of PKStrokePath runs along the polyline itself [R9].
    static func rebuild(paths: [[CGPoint]], widths: [[CGFloat]]?, width: CGFloat, color: RGB, opacity: CGFloat,
                        highlighter: Bool, geometry: PageGeometry) -> [PKStroke] {
        []
    }

    /// The stored strokes with the colour, opacity and highlighter flag the file now has, and every point's size
    /// multiplied by widthScale. A highlighter becomes .marker; a former marker that is no longer one becomes .pen.
    static func restyle(_ strokes: [PKStroke], color: RGB, opacity: CGFloat, highlighter: Bool, widthScale: CGFloat) -> [PKStroke] {
        strokes
    }

    /// "zenith-" + UUID from SHA-256 of creation date, point count, first point.
    static func identifier(for stroke: PKStroke) -> String {
        "zenith-"
    }

    static func style(of ink: PKInk) -> (color: RGB, opacity: CGFloat, highlighter: Bool) {
        (color: .black, opacity: 1, highlighter: false)
    }
}
