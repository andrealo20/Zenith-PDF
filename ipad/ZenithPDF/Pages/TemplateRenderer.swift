import PDFKit
import UIKit

/// Template PDF pages and previews.
enum TemplateRenderer {
    /// UIGraphicsPDFRenderer, at least one page.
    static func pdfData(for template: PaperTemplate, pageCount: Int = 1) -> Data {
        let bounds = CGRect(origin: .zero, size: template.size.points)
        let renderer = UIGraphicsPDFRenderer(bounds: bounds)
        return renderer.pdfData { context in
            for _ in 0..<max(pageCount, 1) {
                context.beginPage()
                draw(template, in: context.cgContext, size: bounds.size)
            }
        }
    }

    /// First page of pdfData, taken out of its temporary document so it can be inserted into a note.
    @MainActor static func page(for template: PaperTemplate) -> PDFPage {
        guard let document = PDFDocument(data: pdfData(for: template)), let page = document.page(at: 0) else {
            return PDFPage()
        }
        document.removePage(at: 0)
        return page
    }

    @MainActor static func preview(for template: PaperTemplate, width: CGFloat) -> UIImage {
        let size = template.size.points
        let scale = max(width, 1) / size.width
        let renderer = UIGraphicsImageRenderer(size: CGSize(width: size.width * scale, height: size.height * scale))
        return renderer.image { context in
            context.cgContext.scaleBy(x: scale, y: scale)
            draw(template, in: context.cgContext, size: size)
        }
    }

    /// Both renderers hand over a context with a top-left origin, the coordinates TemplateGeometry uses.
    private static func draw(_ template: PaperTemplate, in context: CGContext, size: CGSize) {
        context.setFillColor(cgColor(template.color.fill))
        context.fill(CGRect(origin: .zero, size: size))

        let marks = TemplateGeometry.marks(for: template.pattern, size: size)
        if !marks.rules.isEmpty {
            context.setStrokeColor(cgColor(template.color.rule))
            context.setLineWidth(marks.ruleWidth)
            context.setLineCap(.butt)
            for rule in marks.rules {
                context.move(to: rule.start)
                context.addLine(to: rule.end)
            }
            context.strokePath()
        }
        if !marks.dots.isEmpty {
            context.setFillColor(cgColor(template.color.rule))
            let radius = marks.dotDiameter / 2
            for dot in marks.dots {
                context.fillEllipse(in: CGRect(x: dot.x - radius, y: dot.y - radius,
                                               width: marks.dotDiameter, height: marks.dotDiameter))
            }
        }
        if let margin = marks.margin {
            context.setStrokeColor(cgColor(template.color.margin))
            context.setLineWidth(marks.marginWidth)
            context.move(to: margin.start)
            context.addLine(to: margin.end)
            context.strokePath()
        }
    }

    private static func cgColor(_ color: RGB) -> CGColor {
        CGColor(red: color.r, green: color.g, blue: color.b, alpha: 1)
    }
}
