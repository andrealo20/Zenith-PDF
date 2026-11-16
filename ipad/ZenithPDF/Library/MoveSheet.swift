import SwiftUI

/// Folder chooser for move: the library root and every folder in it, nested ones indented.
struct MoveSheet: View {
    private let model: LibraryModel
    private let item: URL
    @Environment(\.dismiss) private var dismiss
    @State private var targets: [Target] = []

    private struct Target: Identifiable {
        let url: URL?                             // nil: the library root
        let name: String
        let depth: Int
        var id: String { url?.path ?? "" }
    }

    init(model: LibraryModel, item: URL) {
        self.model = model
        self.item = item
    }

    var body: some View {
        NavigationStack {
            List(targets) { target in
                Button {
                    model.move(item, to: target.url)
                    dismiss()
                } label: {
                    Label(target.name, systemImage: target.url == nil ? "books.vertical" : "folder")
                        .padding(.leading, CGFloat(target.depth) * 20)
                }
                .disabled(isCurrentParent(target))
            }
            .navigationTitle(tr("Move to"))
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
            }
        }
        .onAppear(perform: loadTargets)
    }

    private func loadTargets() {
        let store = model.store
        var result = [Target(url: nil, name: tr("Library"), depth: 0)]
        func add(_ folder: URL?, depth: Int) {
            for child in (try? store.folders(in: folder)) ?? [] {
                // A folder cannot go into itself or into a folder inside it.
                if SidecarLayout.isInside(child.url, root: item) { continue }
                result.append(Target(url: child.url, name: child.name, depth: depth))
                add(child.url, depth: depth + 1)
            }
        }
        add(nil, depth: 1)
        targets = result
    }

    private func isCurrentParent(_ target: Target) -> Bool {
        SidecarLayout.isInside(item.deletingLastPathComponent(), root: target.url ?? model.location.root)
            && SidecarLayout.isInside(target.url ?? model.location.root, root: item.deletingLastPathComponent())
    }
}
