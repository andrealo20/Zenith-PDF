import SwiftUI

/// NavigationSplitView, sidebar, toolbar.
struct LibraryView: View {
    private let model: LibraryModel
    private let open: (_ note: URL, _ newWindow: Bool) -> Void

    init(model: LibraryModel, open: @escaping (_ note: URL, _ newWindow: Bool) -> Void) {
        self.model = model
        self.open = open
    }

    var body: some View {
        NavigationStack {
            VStack(spacing: 12) {
                Image(systemName: "doc.richtext")
                    .font(.system(size: 44))
                    .foregroundStyle(Folio.textMuted)
                Text("No notes yet")
                    .foregroundStyle(Folio.textMuted)
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .background(Folio.window)
            .navigationTitle("Library")
        }
    }
}
