import PencilKit
import SwiftUI

/// Thumbnails sidebar UI.
struct PageSidebar: View {
    let document: NoteDocument
    let currentPage: Int
    let onSelect: (Int) -> Void

    init(document: NoteDocument, currentPage: Int, onSelect: @escaping (Int) -> Void) {
        self.document = document
        self.currentPage = currentPage
        self.onSelect = onSelect
    }

    /// List of thumbnails, .onMove -> reorderPage, context menu: Add Page After, Duplicate, Delete.
    var body: some View {
        let pageCount = document.pages.count
        List {
            ForEach(document.pages) { page in
                let index = document.index(ofPageID: page.id) ?? 0
                PageSidebarRow(document: document, pageID: page.id, index: index, isCurrent: index == currentPage)
                    .contentShape(Rectangle())
                    .onTapGesture { onSelect(index) }
                    .listRowBackground(index == currentPage ? Folio.selection : Color.clear)
                    .listRowSeparator(.hidden)
                    .contextMenu {
                        Button {
                            document.addPage(at: index + 1)
                        } label: {
                            Label("Add Page After", systemImage: "doc.badge.plus")
                        }
                        Button {
                            document.duplicatePage(at: index)
                        } label: {
                            Label("Duplicate", systemImage: "plus.square.on.square")
                        }
                        Button(role: .destructive) {
                            document.deletePage(at: index)
                        } label: {
                            Label("Delete", systemImage: "trash")
                        }
                        .disabled(pageCount < 2 || document.isReadOnly)
                    }
            }
            .onMove { source, destination in
                guard let from = source.first else { return }
                // SwiftUI gives the offset before removal; reorderPage wants the final index.
                document.reorderPage(from: from, to: destination > from ? destination - 1 : destination)
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .background(Folio.surface)
        .accessibilityIdentifier("pageSidebar")
    }
}

private struct PageSidebarRow: View {
    let document: NoteDocument
    let pageID: UUID
    let index: Int
    let isCurrent: Bool
    @State private var image: UIImage?

    private static let thumbnailWidth: CGFloat = 140

    private struct RenderKey: Equatable {
        let pageID: UUID
        let index: Int
        let revision: Int
    }

    var body: some View {
        let label = tr("Page %lld", index + 1)
        VStack(spacing: 6) {
            thumbnail
                .frame(width: PageSidebarRow.thumbnailWidth)
                .overlay(
                    RoundedRectangle(cornerRadius: 2)
                        .stroke(isCurrent ? Folio.accent : Folio.border, lineWidth: isCurrent ? 2 : 0.5)
                )
                .shadow(color: Color.black.opacity(0.12), radius: 4, x: 0, y: 2)
            Text(label)
                .font(.caption)
                .foregroundStyle(isCurrent ? Folio.accentInk : Folio.textMuted)
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 6)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(Text(label))
        .accessibilityAddTraits(isCurrent ? AccessibilityTraits.isSelected : AccessibilityTraits())
        .task(id: RenderKey(pageID: pageID, index: index, revision: document.revision)) {
            // Rendered again a moment after the last change, so writing does not redraw thumbnails per stroke.
            if image != nil { try? await Task.sleep(nanoseconds: 300_000_000) }
            if Task.isCancelled { return }
            await render()
        }
    }

    @ViewBuilder private var thumbnail: some View {
        if let image {
            Image(uiImage: image)
                .resizable()
                .aspectRatio(contentMode: .fit)
        } else {
            let size = pageSize
            Rectangle()
                .fill(Color.white)
                .aspectRatio(size.width / max(size.height, 1), contentMode: .fit)
        }
    }

    private var pageSize: CGSize {
        guard let index = document.index(ofPageID: pageID) else { return PaperSize.a4.points }
        return document.geometry(at: index).displaySize
    }

    private func render() {
        guard let index = document.index(ofPageID: pageID), let page = document.pdf.page(at: index) else { return }
        image = PageThumbnailer.thumbnail(page: page, drawing: document.drawing(forPageID: pageID),
                                          geometry: document.geometry(at: index), width: PageSidebarRow.thumbnailWidth)
    }
}
