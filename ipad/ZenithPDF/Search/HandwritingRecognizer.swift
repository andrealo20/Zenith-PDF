import CoreGraphics
import PencilKit
import UIKit
import Vision

/// Vision on rendered ink.
enum HandwritingRecognizer {
    /// The ink on white, light appearance, at `scale` (main actor: PencilKit rendering).
    @MainActor static func image(of drawing: PKDrawing, pageSize: CGSize, scale: CGFloat = 2) -> CGImage? {
        guard pageSize.width > 0, pageSize.height > 0, scale > 0 else { return nil }
        let bounds = CGRect(origin: .zero, size: pageSize)
        var ink: UIImage?
        // Pitfall P7: without a light trait collection, black ink renders white in dark mode.
        UITraitCollection(userInterfaceStyle: .light).performAsCurrent {
            ink = drawing.image(from: bounds, scale: scale)
        }
        let format = UIGraphicsImageRendererFormat()
        format.scale = scale
        format.opaque = true
        let image = UIGraphicsImageRenderer(size: pageSize, format: format).image { context in
            UIColor.white.setFill()
            context.fill(bounds)
            ink?.draw(in: bounds)
        }
        return image.cgImage
    }

    /// RecognizeTextRequest, accurate, language correction on, languages detected automatically [R18].
    /// Throws, or returns [], when Vision is unavailable; callers treat both as "no handwriting found".
    static func words(in image: CGImage, pageSize: CGSize) async throws -> [InkWord] {
        var request = RecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        request.automaticallyDetectsLanguage = true
        let observations = try await request.perform(on: image, orientation: nil)

        let imageSize = CGSize(width: image.width, height: image.height)
        let scaleX = pageSize.width > 0 ? imageSize.width / pageSize.width : 1
        let scaleY = pageSize.height > 0 ? imageSize.height / pageSize.height : 1
        var words: [InkWord] = []
        for observation in observations {
            guard let text = observation.topCandidates(1).first?.string, !text.isEmpty else { continue }
            let pixels = observation.boundingBox.toImageCoordinates(imageSize, origin: .upperLeft)
            let line = CGRect(x: pixels.minX / scaleX, y: pixels.minY / scaleY,
                              width: pixels.width / scaleX, height: pixels.height / scaleY)
            // Vision gives one box per line: each word gets the share of it its characters take up.
            let total = CGFloat(text.count)
            var offset = 0
            for part in text.split(separator: " ", omittingEmptySubsequences: false) {
                if !part.isEmpty {
                    words.append(InkWord(text: String(part),
                                         rect: CGRect(x: line.minX + line.width * CGFloat(offset) / total, y: line.minY,
                                                      width: line.width * CGFloat(part.count) / total,
                                                      height: line.height)))
                }
                offset += part.count + 1
            }
        }
        return words
    }
}
