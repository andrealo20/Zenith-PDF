import PDFKit
import PencilKit

enum NoteReader {
    /// Takes the ink out of the displayed document: every visible /Ink annotation becomes strokes and is
    /// removed (with its popup); every /Stamp with /ZenithImageData becomes an ImageAnnotation in place.
    /// Returns one NotePage per PDF page, in order. Nothing is interpolated here (section 4.3).
    @MainActor static func extract(from pdf: PDFDocument) -> [NotePage] {
        var result: [NotePage] = []
        for index in 0..<pdf.pageCount {
            guard let page = pdf.page(at: index) else {
                result.append(NotePage())
                continue
            }
            let geometry = PageGeometry(page: page)
            let raw = PDFRaw.annotations(of: page)
            var strokes: [PKStroke] = []
            // PDFKit drops /NM when it saves; each annotation keeps its id under the stand-in NoteWriter patches back.
            let all = page.annotations
            if all.count == raw.count {
                for (live, entry) in zip(all, raw) where entry.subtype != "Ink" {
                    if let identifier = entry.identifier {
                        _ = live.setValue(identifier as NSString, forAnnotationKey: WrittenKeys.identifier)
                    }
                    // PDFKit keeps /LE on a Line but drops it from a PolyLine.
                    if entry.subtype == "PolyLine", let endings = entry.lineEndings, !endings.isEmpty {
                        _ = live.setValue(endings.map { $0 as NSString } as NSArray, forAnnotationKey: WrittenKeys.lineEndings)
                    }
                }
            }

            let rawInks = raw.filter { $0.subtype == "Ink" }
            let liveInks = page.annotations.filter { $0.type == "Ink" }
            let sameCount = liveInks.count == rawInks.count
            var taken: [PDFAnnotation] = []
            for (position, ink) in rawInks.enumerated() where !ink.isHidden {
                strokes += StrokeCodec.strokes(from: ink, geometry: geometry)
                if let live = match(ink, position: position, among: liveInks, sameCount: sameCount, taken: taken) {
                    taken.append(live)
                }
            }
            for live in taken {
                if let popup = live.popup { page.removeAnnotation(popup) }
                page.removeAnnotation(live)
            }

            let rawStamps = raw.filter { $0.subtype == "Stamp" }
            let liveStamps = page.annotations.filter { $0.type == "Stamp" }
            let sameStamps = liveStamps.count == rawStamps.count
            var replaced: [PDFAnnotation] = []
            for (position, stamp) in rawStamps.enumerated() {
                guard let text = stamp.imageData, let data = Data(base64Encoded: text), !data.isEmpty,
                      let live = match(stamp, position: position, among: liveStamps, sameCount: sameStamps,
                                       taken: replaced),
                      !(live is ImageAnnotation) else { continue }
                replaced.append(live)
                let picture = ImageAnnotation(bounds: live.bounds, imageData: data)
                if let identifier = stamp.identifier {
                    _ = picture.setValue(identifier as NSString, forAnnotationKey: ZenithKey.identifier)
                    _ = picture.setValue(identifier as NSString, forAnnotationKey: WrittenKeys.identifier)
                }
                if let popup = live.popup { page.removeAnnotation(popup) }
                page.removeAnnotation(live)
                page.addAnnotation(picture)
            }
            result.append(NotePage(drawing: PKDrawing(strokes: strokes)))
        }
        return result
    }

    /// The live annotation for a raw one: the same /NM, else the same position among annotations of its kind.
    @MainActor private static func match(_ raw: RawAnnotation, position: Int, among live: [PDFAnnotation],
                                         sameCount: Bool, taken: [PDFAnnotation]) -> PDFAnnotation? {
        let free = { (annotation: PDFAnnotation) in !taken.contains { $0 === annotation } }
        if let identifier = raw.identifier,
           let found = live.first(where: { free($0) && (($0.value(forAnnotationKey: ZenithKey.identifier) as? String) == identifier) }) {
            return found
        }
        guard sameCount, live.indices.contains(position), free(live[position]) else { return nil }
        return live[position]
    }
}
