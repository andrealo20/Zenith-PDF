import SwiftUI

/// Library search results: notes whose title matches, then notes with hits in typed text or handwriting.
struct SearchResultsView: View {
    private let model: LibraryModel
    private let open: (URL, Bool) -> Void

    /// Snippets shown under one note; the rest are found inside the note with its own search.
    private static let hitsShown = 3

    init(model: LibraryModel, open: @escaping (URL, Bool) -> Void) {
        self.model = model
        self.open = open
    }

    var body: some View {
        List {
            Section {
                if model.searchResults.isEmpty {
                    if model.isSearching {
                        ProgressView()
                    } else {
                        Text("No results").foregroundStyle(Folio.textMuted)
                    }
                }
                ForEach(model.searchResults) { result in row(result) }
            } header: {
                Text(tr("Search"))
                    .folioTitle()
                    .textCase(nil)
                    .padding(.bottom, 8)
            }
        }
        .scrollContentBackground(.hidden)
        .background(Folio.window)
        .onAppear(perform: checkReady)
        .onChange(of: model.searchResults) { checkReady() }
    }

    private func row(_ result: LibrarySearchResult) -> some View {
        Button {
            open(result.note.url, false)
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                HStack(spacing: 6) {
                    Image(systemName: "doc").foregroundStyle(Folio.textMuted)
                    Text(result.note.title)
                        .font(.headline)
                        .foregroundStyle(Folio.text)
                    if !result.note.folderPath.isEmpty {
                        Text(result.note.folderPath)
                            .font(.caption)
                            .foregroundStyle(Folio.textMuted)
                    }
                }
                ForEach(result.hits.prefix(SearchResultsView.hitsShown)) { hit in
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Text(tr("Page %lld", hit.pageIndex + 1))
                            .font(.caption.weight(.semibold))
                            .foregroundStyle(Folio.textMuted)
                        Text(hit.snippet)
                            .font(.subheadline)
                            .foregroundStyle(Folio.text)
                            .lineLimit(2)
                        if hit.inInk {
                            Text("In handwriting")
                                .font(.caption)
                                .foregroundStyle(Folio.accentInk)
                        }
                    }
                }
            }
            .padding(.vertical, 4)
        }
        .buttonStyle(.plain)
        .listRowBackground(Folio.raised)
        .accessibilityIdentifier("result:" + result.note.title)
        .contextMenu {
            Button { open(result.note.url, false) } label: { Label("Open", systemImage: "doc") }
            Button { open(result.note.url, true) } label: { Label("Open in New Window", systemImage: "rectangle.split.2x1") }
        }
    }

    private func checkReady() {
        if !model.searchResults.isEmpty { model.markReady(.search) }
    }
}
