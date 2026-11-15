import PDFKit
import UIKit

/// Pages and documents from images.
enum PageFactory {
    /// PDFPage(image:) [R26]
    @MainActor static func page(from image: UIImage) -> PDFPage? {
        nil
    }

    /// A new note from photos or scans.
    @MainActor static func pdfData(from images: [UIImage]) -> Data? {
        nil
    }
}
