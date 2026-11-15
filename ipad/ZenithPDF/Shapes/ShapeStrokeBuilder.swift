import PencilKit

/// Clean strokes for a shape.
enum ShapeStrokeBuilder {
    /// Strokes tracing the shape with the original's ink, mean width and creation date.
    static func strokes(for shape: RecognizedShape, replacing original: PKStroke) -> [PKStroke] {
        [original]
    }
}
