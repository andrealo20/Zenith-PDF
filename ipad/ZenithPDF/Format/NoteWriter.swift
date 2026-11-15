import Foundation
import PDFKit
import PencilKit

/// What a save needs, taken on the main actor in one go.
struct NoteSnapshot: @unchecked Sendable {
    let pdfData: Data                 // the live document's bytes (text boxes, pictures, no ink): NoteDocument's cache
    let drawings: [PKDrawing]         // one per page
    let geometries: [PageGeometry]    // one per page
    let pageCount: Int
    let noteID: String
}

/// A version of the file on disk this app wrote or read.
struct SavedFile: Equatable, Sendable {
    let url: URL
    let modified: Date
    let hash: String                  // FileStore.sha256 of the bytes
}

struct WriteResult: Sendable {
    let saved: SavedFile              // the file written: the note, or the conflict copy
    let conflictWith: URL?            // the original when the data went to a conflict copy, else nil
}

enum NoteWriter {
    /// Builds the file: PDFDocument(data:) copy, documentAttributes[ZenithKey.noteID] = noteID, one InkAnnotation per
    /// record appended to each page, dataRepresentation(). Throws ZenithError.cannotSave if the result does not
    /// reopen with pageCount pages.
    static func render(_ snapshot: NoteSnapshot) throws -> Data {
        let failed = { (reason: String) in ZenithError.cannotSave(URL(fileURLWithPath: "/"), reason: reason) }
        guard let document = PDFDocument(data: snapshot.pdfData) else { throw failed("The note's pages could not be read.") }
        guard document.pageCount == snapshot.pageCount else { throw failed("The page count changed.") }
        var attributes = document.documentAttributes ?? [:]
        attributes[ZenithKey.noteID] = snapshot.noteID
        document.documentAttributes = attributes
        // /NM must be unique in the whole file: the same stroke on two pages (a duplicated page) gets a suffix.
        var used: Set<String> = []
        for index in 0..<snapshot.pageCount {
            guard let page = document.page(at: index) else { throw failed("A page is missing.") }
            let drawing = snapshot.drawings.indices.contains(index) ? snapshot.drawings[index] : PKDrawing()
            let geometry = snapshot.geometries.indices.contains(index) ? snapshot.geometries[index] : PageGeometry(page: page)
            for var record in StrokeCodec.records(for: drawing, geometry: geometry) {
                let base = record.identifier
                var copy = 2
                while used.contains(record.identifier) {
                    record.identifier = "\(base)-p\(copy)"
                    copy += 1
                }
                used.insert(record.identifier)
                page.addAnnotation(InkAnnotation(record: record))
            }
        }
        guard var data = document.dataRepresentation() else { throw failed("PDFKit wrote nothing.") }
        WrittenKeys.patch(&data)
        guard let check = PDFDocument(data: data), check.pageCount == snapshot.pageCount else {
            throw failed("The written file does not reopen with every page.")
        }
        return data
    }

    /// render, then the content check of section 4.7 against `known`, then FileStore.replace. When the file on disk
    /// is no longer `known` the data goes to "<name> (iPad).pdf" beside it and conflictWith is set.
    static func write(_ snapshot: NoteSnapshot, to url: URL, known: SavedFile?, keepPrevious: URL?) throws -> WriteResult {
        let data: Data
        do {
            data = try render(snapshot)
        } catch ZenithError.cannotSave(_, let reason) {
            throw ZenithError.cannotSave(url, reason: reason)
        }
        let hash = FileStore.sha256(data)
        if let known, FileStore.exists(url) {
            let conflicts = FileStore.hasUnresolvedConflicts(url)
            let dateMoved = FileStore.modificationDate(url).map { abs($0.timeIntervalSince(known.modified)) > 1 } ?? true
            if dateMoved || conflicts {
                let current = try FileStore.read(url)
                if conflicts || FileStore.sha256(current) != known.hash {
                    let name = url.deletingPathExtension().lastPathComponent + " (iPad).pdf"
                    let copy = FileStore.uniqueURL(url.deletingLastPathComponent().appendingPathComponent(name, isDirectory: false))
                    let modified = try FileStore.replace(copy, with: data)
                    return WriteResult(saved: SavedFile(url: copy, modified: modified, hash: hash), conflictWith: url)
                }
            }
        }
        let modified = try FileStore.replace(url, with: data, keepPrevious: keepPrevious)
        return WriteResult(saved: SavedFile(url: url, modified: modified, hash: hash), conflictWith: nil)
    }
}

/// Keys PDFKit will not write as given: it refuses /CA, silently drops /NM, and writes a custom document information
/// key as a string instead of a name. They go in under stand-in names of the same length, and the bytes PDFKit wrote
/// are patched in place, so every offset in the cross-reference table stays right.
enum WrittenKeys {
    static let identifier = PDFAnnotationKey(rawValue: "/ZenithPadNM")
    static let opacity = PDFAnnotationKey(rawValue: "/ZenithPadCA")
    static let lineEndings = PDFAnnotationKey(rawValue: "/ZenithPadLE")   // an array of strings, patched to names

    private static let replacements: [(Data, Data)] = [
        (Data("/ZenithPadNM".utf8), Data("/NM         ".utf8)),
        (Data("/ZenithPadCA".utf8), Data("/CA         ".utf8)),
        (Data("(ZenithNoteID) (".utf8), Data("/ZenithNoteID  (".utf8)),
    ]

    static func patch(_ data: inout Data) {
        patchNameArrays(&data)
        for (from, to) in replacements {
            var start = data.startIndex
            while let found = data.range(of: from, options: [], in: start..<data.endIndex) {
                data.replaceSubrange(found, with: to)
                start = found.upperBound
            }
        }
    }

    /// "/ZenithPadLE [ (None) (OpenArrow) ]" becomes "/LE          [ /None  /OpenArrow  ]".
    private static func patchNameArrays(_ data: inout Data) {
        let key = Data("/ZenithPadLE".utf8)
        let open = UInt8(ascii: "["), close = UInt8(ascii: "]")
        let left = UInt8(ascii: "("), right = UInt8(ascii: ")")
        var start = data.startIndex
        while let found = data.range(of: key, options: [], in: start..<data.endIndex) {
            data.replaceSubrange(found, with: Data("/LE         ".utf8))
            start = found.upperBound
            guard let first = data[start...].firstIndex(of: open), data[start..<first].allSatisfy({ $0 == 0x20 || $0 == 0x0A || $0 == 0x0D }),
                  let last = data[first...].firstIndex(of: close) else { continue }
            for index in first..<last {
                if data[index] == left { data[index] = UInt8(ascii: "/") } else if data[index] == right { data[index] = 0x20 }
            }
            start = last
        }
    }
}
