import Darwin
import Foundation
import PDFKit
import PencilKit
import UIKit
import XCTest
@testable import ZenithPDF

/// Finds the test bundle: Bundle(for: TestSupportAnchor.self).
final class TestSupportAnchor {}

enum TestSupport {
    /// Fresh, under NSTemporaryDirectory().
    static func temporaryFolder(_ name: String = #function) -> URL {
        let allowed = CharacterSet.alphanumerics
        let safe = String(name.unicodeScalars.map { allowed.contains($0) ? Character($0) : "-" })
        let url = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent("ZenithTests", isDirectory: true)
            .appendingPathComponent(safe + "-" + UUID().uuidString, isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        } catch {
            XCTFail("Cannot create \(url.path): \(error)")
        }
        return url
    }

    /// Inside the Fixtures folder reference.
    static func fixture(_ name: String) -> URL {
        let bundle = Bundle(for: TestSupportAnchor.self)
        let folder = bundle.url(forResource: "Fixtures", withExtension: nil)
            ?? bundle.bundleURL.appendingPathComponent("Fixtures", isDirectory: true)
        return folder.appendingPathComponent(name, isDirectory: false)
    }

    /// Bundle.main "desktop-ink.pdf", nil when not generated.
    static var desktopFixture: URL? {
        Bundle.main.url(forResource: "desktop-ink", withExtension: "pdf")
            ?? Bundle.main.url(forResource: "desktop-ink", withExtension: "pdf", subdirectory: "Demo")
    }

    /// ProcessInfo environment ZENITH_EXPORT_DIR.
    static var exportFolder: URL? {
        guard let path = ProcessInfo.processInfo.environment["ZENITH_EXPORT_DIR"], !path.isEmpty else { return nil }
        return URL(fileURLWithPath: path, isDirectory: true)
    }

    /// A stroke through `points` used as control points, one every 0.01 s, every point `width` wide.
    static func stroke(_ points: [CGPoint], width: CGFloat = 2, ink: PKInkingTool.InkType = .pen,
                       color: UIColor = .black, date: Date = Date()) -> PKStroke {
        let controlPoints = points.enumerated().map { item in
            PKStrokePoint(location: item.element, timeOffset: TimeInterval(item.offset) * 0.01,
                          size: CGSize(width: width, height: width), opacity: 1, force: 1, azimuth: 0, altitude: .pi / 2)
        }
        let path = PKStrokePath(controlPoints: controlPoints, creationDate: date)
        return PKStroke(ink: PKInk(ink, color: color), path: path, transform: .identity, mask: nil)
    }

    /// Blank pages of `size`, each with /Rotate `rotation`.
    static func pdf(pages: Int, size: CGSize = PaperSize.a4.points, rotation: Int = 0) -> PDFDocument {
        let renderer = UIGraphicsPDFRenderer(bounds: CGRect(origin: .zero, size: size))
        let data = renderer.pdfData { context in
            for _ in 0..<max(pages, 0) { context.beginPage() }
        }
        let document = PDFDocument(data: data) ?? PDFDocument()
        for index in 0..<document.pageCount {
            document.page(at: index)?.rotation = rotation
        }
        return document
    }

    /// Writes `pdf` as "<name>.pdf" in `folder` (created when missing) and returns its URL.
    static func writeNote(_ pdf: PDFDocument, named name: String, in folder: URL) -> URL {
        let fileName = name.lowercased().hasSuffix(".pdf") ? name : name + ".pdf"
        let url = folder.appendingPathComponent(fileName, isDirectory: false)
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        } catch {
            XCTFail("Cannot create \(folder.path): \(error)")
        }
        if !pdf.write(to: url) { XCTFail("Cannot write \(url.path)") }
        return url
    }

    /// task_info TASK_VM_INFO phys_footprint, in bytes; 0 when the call fails.
    static func physicalFootprint() -> UInt64 {
        var info = task_vm_info_data_t()
        var count = mach_msg_type_number_t(MemoryLayout<task_vm_info_data_t>.size / MemoryLayout<integer_t>.size)
        let result = withUnsafeMutablePointer(to: &info) { pointer in
            pointer.withMemoryRebound(to: integer_t.self, capacity: Int(count)) { raw in
                task_info(mach_task_self_, task_flavor_t(TASK_VM_INFO), raw, &count)
            }
        }
        return result == KERN_SUCCESS ? UInt64(info.phys_footprint) : 0
    }
}
