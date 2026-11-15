import CoreGraphics

enum RecognizedShape: Equatable {
    case line(CGPoint, CGPoint)
    case rectangle(CGRect)
    case ellipse(CGRect)
    case polygon([CGPoint])
}

/// Port of inking.as_line / as_shape.
enum ShapeRecognizer {
    /// Port of inking.as_line then inking.as_shape; `unit` is the size of one screen pixel in points (1 / zoom).
    static func recognize(_ points: [CGPoint], unit: CGFloat) -> RecognizedShape? {
        nil
    }

    static func asLine(_ points: [CGPoint], unit: CGFloat) -> (CGPoint, CGPoint)? {
        nil
    }

    static func asShape(_ points: [CGPoint], unit: CGFloat) -> RecognizedShape? {
        nil
    }
}
