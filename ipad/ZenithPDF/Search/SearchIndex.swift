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

    /// nil when the sidecar is missing, unreadable or written by a newer version.
    static func load(note: URL, libraryRoot: URL?) -> SearchIndex? {
        let url = SidecarLayout.searchIndex(forNote: note, libraryRoot: libraryRoot)
        guard let data = try? Data(contentsOf: url),
              let index = try? JSONDecoder().decode(SearchIndex.self, from: data),
              index.version == 1 else { return nil }
        return index
    }

    func save(note: URL, libraryRoot: URL?) throws {
        let url = SidecarLayout.searchIndex(forNote: note, libraryRoot: libraryRoot)
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try JSONEncoder().encode(self).write(to: url, options: .atomic)
    }

    /// Every query word, folded, as a prefix of a word on the page.
    /// Per matching page: one ink hit with the rects of the matching ink words, then one typed hit.
    func hits(for query: String) -> [SearchHit] {
        let wanted = Self.words(in: query).map(\.folded)
        guard !wanted.isEmpty else { return [] }
        func matches(_ word: String) -> Bool { wanted.contains { word.hasPrefix($0) } }

        var hits: [SearchHit] = []
        for (pageIndex, page) in pages.enumerated() {
            let typed = Self.words(in: page.typed)
            let ink = page.words.map { Self.words(in: $0.text).map(\.folded) }
            let onPage = wanted.allSatisfy { prefix in
                typed.contains { $0.folded.hasPrefix(prefix) } || ink.contains { $0.contains { $0.hasPrefix(prefix) } }
            }
            guard onPage else { continue }

            var n = 0
            let inkMatches = page.words.indices.filter { ink[$0].contains(where: matches) }
            if let first = inkMatches.first {
                let lower = max(0, first - 4), upper = min(page.words.count, first + 6)
                var snippet = page.words[lower..<upper].map(\.text).joined(separator: " ")
                if lower > 0 { snippet = "\u{2026}" + snippet }
                if upper < page.words.count { snippet += "\u{2026}" }
                hits.append(SearchHit(id: "\(pageIndex)-\(n)", pageIndex: pageIndex, snippet: snippet,
                                      rects: inkMatches.map { page.words[$0].rect }, inInk: true))
                n += 1
            }
            if let first = typed.first(where: { matches($0.folded) }) {
                hits.append(SearchHit(id: "\(pageIndex)-\(n)", pageIndex: pageIndex,
                                      snippet: Self.snippet(page.typed, around: first.range), rects: [], inInk: false))
            }
        }
        return hits
    }

    /// Words as the system segments them (punctuation dropped, any script), each with its folded form.
    private static func words(in text: String) -> [(range: Range<String.Index>, folded: String)] {
        var words: [(range: Range<String.Index>, folded: String)] = []
        text.enumerateSubstrings(in: text.startIndex..<text.endIndex, options: .byWords) { word, range, _, _ in
            if let word, !word.isEmpty { words.append((range, word.searchFolded)) }
        }
        return words
    }

    /// About 40 characters before and 60 after the match, whitespace collapsed.
    private static func snippet(_ text: String, around range: Range<String.Index>) -> String {
        let start = text.index(range.lowerBound, offsetBy: -40, limitedBy: text.startIndex) ?? text.startIndex
        let end = text.index(range.upperBound, offsetBy: 60, limitedBy: text.endIndex) ?? text.endIndex
        var snippet = text[start..<end].split(whereSeparator: { $0.isWhitespace }).joined(separator: " ")
        if start > text.startIndex { snippet = "\u{2026}" + snippet }
        if end < text.endIndex { snippet += "\u{2026}" }
        return snippet
    }
}

extension String {
    /// Case and diacritics folded, locale nil.
    var searchFolded: String { folding(options: [.caseInsensitive, .diacriticInsensitive], locale: nil) }
}
