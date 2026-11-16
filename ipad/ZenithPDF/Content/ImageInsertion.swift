import PDFKit
import UIKit

/// Place a picture on the page.
@MainActor enum ImageInsertion {
    /// Centred on the current page, at most 60% of its width, undoable.
    static func insert(_ image: UIImage, into editor: EditorViewController) {
        let document = editor.document
        guard !document.isReadOnly, image.size.width > 0, image.size.height > 0,
              let page = document.pdf.page(at: editor.currentPageIndex) ?? document.pdf.page(at: 0),
              let data = ImageAnnotation.preparedData(for: image) else { return }
        let bounds = placement(of: image.size, in: PageGeometry(page: page).box)
        SelectionController.changePresence(ImageAnnotation(bounds: bounds, imageData: data), on: page, present: true,
                                           document: document, actionName: tr("Insert Photo"))
    }

    /// shortcut: sized and centred in PDF space, so on a page with /Rotate the picture turns with the page;
    /// place it in display space if rotated pages turn out to need pictures upright.
    private static func placement(of size: CGSize, in box: CGRect) -> CGRect {
        let aspect = size.height / size.width
        var width = min(size.width, box.width * 0.6)
        var height = width * aspect
        // A tall picture also stays clear of the page's top and bottom.
        if height > box.height * 0.8 {
            height = box.height * 0.8
            width = height / aspect
        }
        return CGRect(x: box.midX - width / 2, y: box.midY - height / 2, width: width, height: height)
    }
}
