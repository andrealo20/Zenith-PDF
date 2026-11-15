import CoreGraphics
import PencilKit

/// Vision on rendered ink.
enum HandwritingRecognizer {
    /// The ink on white, light appearance, at `scale` (main actor: PencilKit rendering).
    @MainActor static func image(of drawing: PKDrawing, pageSize: CGSize, scale: CGFloat = 2) -> CGImage? {
        nil
    }

    /// RecognizeTextRequest, accurate, language correction on, languages detected automatically [R18].
    /// Throws, or returns [], when Vision is unavailable; callers treat both as "no handwriting found".
    static func words(in image: CGImage, pageSize: CGSize) async throws -> [InkWord] {
        []
    }
}
