import PDFKit
import PencilKit
import UIKit

/// Page + ink thumbnails.
@MainActor enum PageThumbnailer {
    static func thumbnail(page: PDFPage, drawing: PKDrawing, geometry: PageGeometry, width: CGFloat) -> UIImage {
        UIImage()
    }
}
