import SwiftUI

/// Font, size, colour, alignment UI.
struct TextStyleBar: View {
    @Binding private var style: TextBoxStyle

    init(style: Binding<TextBoxStyle>) {
        self._style = style
    }

    var body: some View {
        EmptyView()
    }
}
