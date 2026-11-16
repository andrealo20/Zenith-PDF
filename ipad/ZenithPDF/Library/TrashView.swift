import SwiftUI

/// Recently Deleted: restore, delete permanently, empty. The last two ask first, since they cannot be undone.
struct TrashView: View {
    private let model: LibraryModel
    @State private var deleting: TrashedItem?
    @State private var emptying = false

    init(model: LibraryModel) {
        self.model = model
    }

    var body: some View {
        List {
            Section {
                if model.trash.isEmpty {
                    Text("No notes yet")
                        .foregroundStyle(Folio.textMuted)
                }
                ForEach(model.trash) { item in row(item) }
            } header: {
                Text(model.title)
                    .folioTitle()
                    .textCase(nil)
                    .padding(.bottom, 8)
            } footer: {
                Text("Items are deleted permanently after 30 days.")
            }
        }
        .scrollContentBackground(.hidden)
        .background(Folio.window)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("Empty Recently Deleted", role: .destructive) { emptying = true }
                    .disabled(model.trash.isEmpty)
            }
        }
        .confirmationDialog("Delete Permanently", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible, presenting: deleting) { item in
            Button("Delete Permanently", role: .destructive) { model.deletePermanently(item) }
            Button("Cancel", role: .cancel) {}
        } message: { _ in
            Text("This cannot be undone.")
        }
        .confirmationDialog("Empty Recently Deleted", isPresented: $emptying, titleVisibility: .visible) {
            Button("Empty Recently Deleted", role: .destructive) { model.emptyTrash() }
            Button("Cancel", role: .cancel) {}
        } message: {
            Text("This cannot be undone.")
        }
        .onAppear(perform: checkReady)
        .onChange(of: model.trash) { checkReady() }
    }

    private func checkReady() {
        if !model.trash.isEmpty { model.markReady(.trash) }
    }

    private func row(_ item: TrashedItem) -> some View {
        HStack(spacing: 12) {
            Image(systemName: item.isFolder ? "folder" : "doc")
                .foregroundStyle(Folio.textMuted)
                .frame(width: 24)
            VStack(alignment: .leading, spacing: 2) {
                Text(item.title)
                    .foregroundStyle(Folio.text)
                Text(item.deletedAt, format: .dateTime.day().month(.abbreviated).year().hour().minute())
                    .font(.caption)
                    .foregroundStyle(Folio.textMuted)
            }
            Spacer()
            Button("Restore") { model.restore(item) }
                .buttonStyle(.bordered)
                .accessibilityIdentifier("restore:" + item.title)
        }
        .listRowBackground(Folio.raised)
        .contextMenu {
            Button { model.restore(item) } label: { Label("Restore", systemImage: "arrow.uturn.backward") }
            Button(role: .destructive) { deleting = item } label: { Label("Delete Permanently", systemImage: "trash") }
        }
        .swipeActions {
            // No destructive role: that role removes the row before the person has confirmed.
            Button("Delete Permanently") { deleting = item }
                .tint(Folio.danger)
        }
    }
}
