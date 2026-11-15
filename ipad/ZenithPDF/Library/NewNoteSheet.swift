import SwiftUI
import UIKit

/// Template chooser: pattern previews, paper colour, size and a name. Starts from the paper used last.
struct NewNoteSheet: View {
    private let model: LibraryModel
    private let onCreated: (URL) -> Void
    @Environment(\.dismiss) private var dismiss
    @State private var template: PaperTemplate
    @State private var name = ""

    private static let previewWidth: CGFloat = 112

    init(model: LibraryModel, onCreated: @escaping (URL) -> Void) {
        self.model = model
        self.onCreated = onCreated
        _template = State(initialValue: model.newNoteTemplate)
    }

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("Note name", text: $name, prompt: Text("Untitled"))
                        .accessibilityIdentifier("noteNameField")
                }
                Section("Paper") {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: NewNoteSheet.previewWidth + 8), spacing: 14)], spacing: 16) {
                        ForEach(PaperPattern.allCases, id: \.self) { pattern in patternButton(pattern) }
                    }
                    .padding(.vertical, 8)
                }
                Section {
                    Picker("Colour", selection: $template.color) {
                        ForEach(PaperColor.allCases, id: \.self) { color in Text(NewNoteSheet.label(color)).tag(color) }
                    }
                    Picker("Size", selection: $template.size) {
                        ForEach(PaperSize.allCases, id: \.self) { size in Text(NewNoteSheet.label(size)).tag(size) }
                    }
                }
                .pickerStyle(.segmented)
            }
            .scrollContentBackground(.hidden)
            .background(Folio.window)
            .navigationTitle("New Note")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) {
                    Button("Cancel") { dismiss() }
                }
                ToolbarItem(placement: .confirmationAction) {
                    Button("Create", action: create)
                        .accessibilityIdentifier("createNoteButton")
                }
            }
        }
        .onAppear { model.markReady(.templates) }
        .alert(model.error?.errorDescription ?? "", isPresented: Binding(get: { model.error != nil },
                                                                         set: { if !$0 { model.error = nil } })) {
            Button("OK", role: .cancel) { model.error = nil }
        }
    }

    private func patternButton(_ pattern: PaperPattern) -> some View {
        let shown = PaperTemplate(pattern: pattern, color: template.color, size: template.size)
        let isSelected = template.pattern == pattern
        return Button {
            template.pattern = pattern
        } label: {
            VStack(spacing: 8) {
                Image(uiImage: TemplateRenderer.preview(for: shown, width: NewNoteSheet.previewWidth))
                    .resizable()
                    .scaledToFit()
                    .frame(width: NewNoteSheet.previewWidth)
                    .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                    .folioCard(radius: 6)
                    .overlay(
                        RoundedRectangle(cornerRadius: 6, style: .continuous)
                            .stroke(isSelected ? Folio.accent : Folio.border, lineWidth: isSelected ? 2.5 : 1)
                    )
                Text(NewNoteSheet.label(pattern))
                    .font(.footnote)
                    .foregroundStyle(isSelected ? Folio.accentInk : Folio.textMuted)
            }
        }
        .buttonStyle(.plain)
        .accessibilityIdentifier("template-" + pattern.rawValue)
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }

    private func create() {
        model.newNoteTemplate = template
        guard let url = model.createNote(template: template, name: name) else { return }
        onCreated(url)
    }

    static func label(_ pattern: PaperPattern) -> String {
        switch pattern {
        case .blank: return tr("Blank")
        case .lined: return tr("Lined")
        case .collegeRuled: return tr("College Ruled")
        case .grid: return tr("Grid")
        case .dotted: return tr("Dotted")
        }
    }

    static func label(_ color: PaperColor) -> String {
        switch color {
        case .white: return tr("White")
        case .cream: return tr("Cream")
        case .dark: return tr("Dark")
        }
    }

    static func label(_ size: PaperSize) -> String {
        switch size {
        case .a4: return tr("A4")
        case .letter: return tr("Letter")
        }
    }
}
