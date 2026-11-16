import Observation
import SwiftUI

/// In-note search model.
@MainActor @Observable final class NoteSearchModel {
    private let document: NoteDocument
    private let editor: EditorViewController

    init(document: NoteDocument, editor: EditorViewController) {
        self.document = document
        self.editor = editor
    }

    var query: String = ""
    private(set) var hits: [SearchHit] = []
    private(set) var isIndexing: Bool = false
    /// What was last handed to editor.setHighlights.
    private(set) var highlights: [Int: [CGRect]] = [:]
    private(set) var selectedHitID: String? = nil
    /// The query `hits` belong to, so the panel does not say "No results" while a new query is pending.
    private(set) var resultsQuery: String = ""

    /// indexNow if stale, then hits; highlights all hits.
    /// indexNow recognises only pages whose ink changed, so on an unchanged note it costs one hash per page.
    func run() async {
        let text = query
        guard !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else {
            show([], for: text)
            return
        }
        isIndexing = true
        await SearchIndexer.shared.indexNow(document)
        isIndexing = false
        guard text == query else { return }   // a newer run owns the results
        let index = SearchIndex.load(note: document.url, libraryRoot: document.libraryRoot)
        show(index?.hits(for: text) ?? [], for: text)
    }

    /// Go to page, highlight that hit.
    func select(_ hit: SearchHit) {
        selectedHitID = hit.id
        highlights = hit.rects.isEmpty ? [:] : [hit.pageIndex: hit.rects]
        editor.setHighlights(highlights)
        editor.go(toPage: hit.pageIndex, animated: true)
    }

    func clear() {
        query = ""
        show([], for: "")
    }

    private func show(_ found: [SearchHit], for text: String) {
        hits = found
        resultsQuery = text
        selectedHitID = nil
        var rects: [Int: [CGRect]] = [:]
        for hit in found where !hit.rects.isEmpty {
            rects[hit.pageIndex, default: []].append(contentsOf: hit.rects)
        }
        highlights = rects
        editor.setHighlights(rects)
    }
}

/// In-note search panel.
struct NoteSearchPanel: View {
    @Bindable private var model: NoteSearchModel

    init(model: NoteSearchModel) {
        _model = Bindable(model)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass")
                    .foregroundStyle(Folio.textMuted)
                TextField("Search in Note", text: $model.query)
                    .textFieldStyle(.plain)
                    .submitLabel(.search)
                    .autocorrectionDisabled()
                    .onSubmit { Task { await model.run() } }
                    .accessibilityIdentifier("noteSearchField")
                if !model.query.isEmpty {
                    Button {
                        model.clear()
                    } label: {
                        Image(systemName: "xmark.circle.fill")
                            .foregroundStyle(Folio.textMuted)
                    }
                    .buttonStyle(.plain)
                    .accessibilityLabel(Text("Cancel"))
                }
            }
            .padding(10)
            .background(Folio.window, in: RoundedRectangle(cornerRadius: 8))
            .padding(12)

            status
                .font(.footnote)
                .foregroundStyle(Folio.textMuted)
                .padding(.horizontal, 16)
                .padding(.bottom, 8)

            ScrollView {
                LazyVStack(spacing: 2) {
                    ForEach(model.hits) { hit in
                        row(hit)
                    }
                }
                .padding(.horizontal, 8)
                .padding(.bottom, 12)
            }
        }
        .frame(maxHeight: .infinity, alignment: .top)
        .background(Folio.raised)
        .accessibilityIdentifier("noteSearch")
        .onDisappear { model.clear() }      // closing the panel takes the highlights off the pages
        .task(id: model.query) {
            // Searches once typing pauses; a new keystroke cancels the wait.
            try? await Task.sleep(nanoseconds: 300_000_000)
            guard !Task.isCancelled else { return }
            await model.run()
        }
    }

    @ViewBuilder private var status: some View {
        if model.isIndexing {
            HStack(spacing: 8) {
                ProgressView()
                Text("Indexing handwriting")
            }
        } else if !model.resultsQuery.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty,
                  model.resultsQuery == model.query {
            Text(model.hits.isEmpty ? tr("No results") : tr("%lld results", Int64(model.hits.count)))
        }
    }

    private func row(_ hit: SearchHit) -> some View {
        Button {
            model.select(hit)
        } label: {
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(tr("Page %lld", Int64(hit.pageIndex + 1)))
                        .font(.caption.weight(.semibold))
                    Spacer()
                    if hit.inInk {
                        Label("In handwriting", systemImage: "pencil.tip")
                            .font(.caption)
                    }
                }
                .foregroundStyle(Folio.textMuted)
                Text(hit.snippet)
                    .font(.callout)
                    .foregroundStyle(Folio.text)
                    .lineLimit(3)
                    .multilineTextAlignment(.leading)
            }
            .padding(10)
            .frame(maxWidth: .infinity, alignment: .leading)
            .background(model.selectedHitID == hit.id ? Folio.selection : Color.clear,
                        in: RoundedRectangle(cornerRadius: 8))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}
