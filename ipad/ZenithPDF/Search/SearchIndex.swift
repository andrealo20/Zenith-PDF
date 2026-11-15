import CoreGraphics
import Foundation

struct InkWord: Codable, Equatable, Sendable { var text: String; var rect: CGRect }    // display space

struct PageText: Codable, Equatable, Sendable {
    var typed: String                 // PDFPage.string
    var words: [InkWord]              // recognised handwriting
    var inkHash: String               // SHA-256 of the page's drawing data, to skip unchanged pages
}

/// Equal and hashed by id only, so nothing depends on CGRect being Hashable (section 9).
struct SearchHit: Identifiable, Hashable, Sendable {
    let id: String                    // "<page>-<n>"
    let pageIndex: Int
    let snippet: String
    let rects: [CGRect]               // display space; empty for typed text hits
    let inInk: Bool

    static func == (a: SearchHit, b: SearchHit) -> Bool {
        a.id == b.id
    }

    func hash(into hasher: inout Hasher) {
        hasher.combine(id)
    }
}

/// Index model, matching, folding.
struct SearchIndex: Codable, Sendable {
    var version: Int = 1
    var pages: [PageText]

    init(pages: [PageText]) {
        self.pages = pages
    }

    static func load(note: URL, libraryRoot: URL?) -> SearchIndex? {
        nil
    }

    func save(note: URL, libraryRoot: URL?) throws {
    }

    /// Every query word, folded, as a prefix of a word on the page.
    func hits(for query: String) -> [SearchHit] {
        []
    }
}

extension String {
    /// Case and diacritics folded, locale nil.
    var searchFolded: String { folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil) }
}
