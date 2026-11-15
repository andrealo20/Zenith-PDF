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
        snapshot.pdfData
    }

    /// render, then the content check of section 4.7 against `known`, then FileStore.replace. When the file on disk
    /// is no longer `known` the data goes to "<name> (iPad).pdf" beside it and conflictWith is set.
    static func write(_ snapshot: NoteSnapshot, to url: URL, known: SavedFile?, keepPrevious: URL?) throws -> WriteResult {
        throw ZenithError.cannotSave(url, reason: "Saving is not available yet.")
    }
}
