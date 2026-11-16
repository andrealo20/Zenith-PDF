import SwiftUI

/// Font, size, colour, alignment UI.
struct TextStyleBar: View {
    @Binding private var style: TextBoxStyle

    private static let sizes: [CGFloat] = [8, 10, 12, 14, 16, 18, 20, 24, 28, 32, 40, 48, 64, 72]
    private static let alignmentNames = ["Align Left", "Align Center", "Align Right"]
    private static let alignmentSymbols = ["text.alignleft", "text.aligncenter", "text.alignright"]

    init(style: Binding<TextBoxStyle>) {
        self._style = style
    }

    var body: some View {
        HStack(spacing: 14) {
            Picker("Font", selection: $style.font) {
                ForEach(TextFont.allCases, id: \.self) { font in
                    Text(verbatim: Self.name(of: font)).tag(font)
                }
            }
            .pickerStyle(.menu)
            .accessibilityIdentifier("textFont")

            Menu {
                ForEach(Self.sizes, id: \.self) { size in
                    Button {
                        style.size = size
                    } label: {
                        Text(verbatim: Self.sizeLabel(size))
                    }
                }
            } label: {
                Text(verbatim: Self.sizeLabel(style.size))
                    .monospacedDigit()
                    .frame(minWidth: 44)
            }
            .accessibilityLabel(Text("Size"))
            .accessibilityIdentifier("textSize")

            ColorPicker(selection: color, supportsOpacity: false) {
                Text("Colour")
            }
            .labelsHidden()
            .accessibilityIdentifier("textColour")

            Divider().frame(height: 22)

            Toggle(isOn: $style.bold) {
                Image(systemName: "bold")
            }
            .toggleStyle(.button)
            .accessibilityLabel(Text("Bold"))
            .accessibilityIdentifier("textBold")

            Toggle(isOn: $style.italic) {
                Image(systemName: "italic")
            }
            .toggleStyle(.button)
            .accessibilityLabel(Text("Italic"))
            .accessibilityIdentifier("textItalic")

            Divider().frame(height: 22)

            Picker("Alignment", selection: $style.alignment) {
                ForEach(0..<3, id: \.self) { value in
                    Image(systemName: Self.alignmentSymbols[value])
                        .accessibilityLabel(Text(verbatim: Self.alignmentNames[value]))
                        .tag(value)
                }
            }
            .pickerStyle(.segmented)
            .frame(width: 132)
            .accessibilityIdentifier("textAlignment")
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 8)
        .foregroundStyle(Folio.textMuted)
        .folioCard(radius: 12)
        .accessibilityIdentifier("textStyleBar")
    }

    /// The colour picker works on SwiftUI colours; the style keeps sRGB components.
    private var color: Binding<Color> {
        Binding(get: { Color(uiColor: style.color.uiColor) },
                set: { style.color = RGB(UIColor($0)) })
    }

    private static func name(of font: TextFont) -> String {
        switch font {
        case .helvetica: return "Helvetica"
        case .times: return "Times"
        case .courier: return "Courier"
        }
    }

    private static func sizeLabel(_ size: CGFloat) -> String {
        let rounded = size.rounded()
        return abs(size - rounded) < 0.05 ? "\(Int(rounded)) pt" : String(format: "%.1f pt", Double(size))
    }
}
