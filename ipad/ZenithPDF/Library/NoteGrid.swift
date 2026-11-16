import SwiftUI
import UIKit

/// Tiles, context menus. Folders come first, then notes in the chosen sort.
struct NoteGrid: View {
    private let model: LibraryModel
    private let open: (URL, Bool) -> Void
    @Environment(\.supportsMultipleWindows) private var supportsMultipleWindows
    @State private var renaming: URL?
    @State private var newName = ""
    @State private var thumbnailsShown: Set<URL> = []

    /// Thumbnails asked by tiles, wide enough for the widest tile the adaptive grid makes on an iPad.
    private static let thumbnailWidth: CGFloat = 260
    /// Tiles whose thumbnails must be drawn before a demo screenshot: the ones on screen at launch.
    private static let readyTileCount = 8

    init(model: LibraryModel, open: @escaping (URL, Bool) -> Void) {
        self.model = model
        self.open = open
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                Text(model.title).folioTitle()
                if model.folders.isEmpty && model.notes.isEmpty {
                    emptyState
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 170), spacing: 22)], alignment: .leading, spacing: 26) {
                        ForEach(model.folders) { folder in folderTile(folder) }
                        ForEach(model.notes) { note in noteTile(note) }
                    }
                }
            }
            .padding(.horizontal, 24)
            .padding(.vertical, 16)
        }
        .background(Folio.window)
        .onAppear(perform: checkReady)
        .onChange(of: model.notes) { checkReady() }
        .onChange(of: thumbnailsShown) { checkReady() }
        .alert("Rename", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Note name", text: $newName)
            Button("Cancel", role: .cancel) { renaming = nil }
            Button("Rename") {
                if let renaming { model.rename(renaming, to: newName) }
                renaming = nil
            }
        }
    }

    private var emptyState: some View {
        VStack(spacing: 12) {
            Image(systemName: "doc.richtext")
                .font(.system(size: 44))
                .foregroundStyle(Folio.textMuted)
            Text("No notes yet")
                .foregroundStyle(Folio.textMuted)
        }
        .frame(maxWidth: .infinity)
        .padding(.top, 120)
    }

    // MARK: - Tiles

    private func noteTile(_ note: NoteItem) -> some View {
        Button {
            if model.isSelecting { toggle(note.url) } else { open(note.url, false) }
        } label: {
            NoteTile(note: note, isSelected: model.selectedURLs.contains(note.url), width: NoteGrid.thumbnailWidth) {
                thumbnailsShown.insert(note.url)
            }
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("note:" + note.title)
        .contextMenu {
            Button { open(note.url, false) } label: { Label("Open", systemImage: "doc") }
            if supportsMultipleWindows {
                Button { open(note.url, true) } label: { Label("Open in New Window", systemImage: "rectangle.split.2x1") }
            }
            Button { beginRename(note.url, current: note.title) } label: { Label("Rename", systemImage: "pencil") }
            Button { model.duplicate(note.url) } label: { Label("Duplicate", systemImage: "plus.square.on.square") }
            Button { model.presentedSheet = .move(note.url) } label: { Label("Move", systemImage: "folder") }
            Button { model.toggleFavourite(note) } label: {
                note.isFavourite
                    ? Label("Remove from Favourites", systemImage: "star.slash")
                    : Label("Favourite", systemImage: "star")
            }
            ShareLink(item: note.url) { Label("Share", systemImage: "square.and.arrow.up") }
            Divider()
            Button(role: .destructive) { model.delete(note.url) } label: { Label("Delete", systemImage: "trash") }
        }
    }

    private func folderTile(_ folder: FolderItem) -> some View {
        Button {
            if model.isSelecting { toggle(folder.url) } else { model.selection = .folder(folder.url) }
        } label: {
            VStack(alignment: .leading, spacing: 6) {
                ZStack {
                    Image(systemName: "folder.fill")
                        .font(.system(size: 54))
                        .foregroundStyle(Folio.textMuted)
                }
                .frame(maxWidth: .infinity, maxHeight: .infinity)
                .aspectRatio(0.75, contentMode: .fit)
                .folioCard()
                .overlay(SelectionMark(isSelected: model.selectedURLs.contains(folder.url)))
                Text(folder.name)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Folio.text)
                    .lineLimit(1)
                Text(folder.noteCount == 1 ? tr("1 note") : tr("%lld notes", folder.noteCount))
                    .font(.caption)
                    .foregroundStyle(Folio.textMuted)
            }
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("folder:" + folder.name)
        .contextMenu {
            Button { model.selection = .folder(folder.url) } label: { Label("Open", systemImage: "folder") }
            Button { beginRename(folder.url, current: folder.name) } label: { Label("Rename", systemImage: "pencil") }
            Button { model.duplicate(folder.url) } label: { Label("Duplicate", systemImage: "plus.square.on.square") }
            Button { model.presentedSheet = .move(folder.url) } label: { Label("Move", systemImage: "folder") }
            Divider()
            Button(role: .destructive) { model.delete(folder.url) } label: { Label("Delete", systemImage: "trash") }
        }
    }

    // MARK: - Private

    private func toggle(_ url: URL) {
        if model.selectedURLs.contains(url) { model.selectedURLs.remove(url) } else { model.selectedURLs.insert(url) }
    }

    private func beginRename(_ url: URL, current: String) {
        newName = current
        renaming = url
    }

    /// The library and empty demo scenes are ready once the first tiles show their thumbnails.
    private func checkReady() {
        let needed = model.notes.prefix(NoteGrid.readyTileCount).map(\.url)
        guard needed.allSatisfy({ thumbnailsShown.contains($0) }) else { return }
        model.markReady(model.notes.isEmpty ? .empty : .library)
    }
}

/// Neutral fill and a check mark over a tile picked in Select mode.
private struct SelectionMark: View {
    let isSelected: Bool

    var body: some View {
        if isSelected {
            RoundedRectangle(cornerRadius: 10, style: .continuous)
                .fill(Folio.selection)
                .overlay(alignment: .topTrailing) {
                    Image(systemName: "checkmark.circle.fill")
                        .font(.title2)
                        .foregroundStyle(Folio.accent)
                        .padding(8)
                }
        }
    }
}

/// Thumbnail card, title, date, a small star when favourite.
private struct NoteTile: View {
    let note: NoteItem
    let isSelected: Bool
    let width: CGFloat
    let onThumbnail: () -> Void
    @State private var image: UIImage? = nil

    init(note: NoteItem, isSelected: Bool, width: CGFloat, onThumbnail: @escaping () -> Void) {
        self.note = note
        self.isSelected = isSelected
        self.width = width
        self.onThumbnail = onThumbnail
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ZStack {
                if let image {
                    Image(uiImage: image)
                        .resizable()
                        .scaledToFit()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .aspectRatio(0.75, contentMode: .fit)
            .clipShape(RoundedRectangle(cornerRadius: 10, style: .continuous))
            .folioCard()
            .overlay(SelectionMark(isSelected: isSelected))
            HStack(spacing: 4) {
                Text(note.title)
                    .font(.subheadline.weight(.semibold))
                    .foregroundStyle(Folio.text)
                    .lineLimit(1)
                if note.isFavourite {
                    Image(systemName: "star.fill")
                        .font(.caption2)
                        .foregroundStyle(Folio.textMuted)
                        .accessibilityLabel("Favourite")
                }
            }
            Text(note.modified, format: .dateTime.day().month(.abbreviated).year())
                .font(.caption)
                .foregroundStyle(Folio.textMuted)
        }
        .task(id: note) {
            image = await ThumbnailCache.shared.thumbnail(for: note.url, modified: note.modified, width: width)
            onThumbnail()
        }
    }
}
