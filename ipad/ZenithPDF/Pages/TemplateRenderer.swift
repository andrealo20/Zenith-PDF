import PDFKit
import UIKit

/// Template PDF pages and previews.
enum TemplateRenderer {
    /// UIGraphicsPDFRenderer
    static func pdfData(for template: PaperTemplate, pageCount: Int = 1) -> Data {
        Data()
    }

    /// First page of pdfData.
    @MainActor static func page(for template: PaperTemplate) -> PDFPage {
        PDFPage()
    }

    @MainActor static func preview(for template: PaperTemplate, width: CGFloat) -> UIImage {
        UIImage()
    }
}
