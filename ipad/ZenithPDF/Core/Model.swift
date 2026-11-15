import Foundation
import PencilKit
import UIKit

struct RGB: Codable, Hashable, Sendable {
    var r: CGFloat; var g: CGFloat; var b: CGFloat

    static let black = RGB(r: 0, g: 0, b: 0)

    init(r: CGFloat, g: CGFloat, b: CGFloat) {
        self.r = r
        self.g = g
        self.b = b
    }

    /// Resolved in the light appearance, sRGB, clamped to 0...1.
    init(_ color: UIColor) {
        let resolved = color.resolvedColor(with: UITraitCollection(userInterfaceStyle: .light))
        var red: CGFloat = 0, green: CGFloat = 0, blue: CGFloat = 0, alpha: CGFloat = 0
        if !resolved.getRed(&red, green: &green, blue: &blue, alpha: &alpha) {
            // Grey and pattern colours: fall back to the CGColor's components.
            let parts = resolved.cgColor.components ?? []
            if parts.count >= 3 {
                red = parts[0]; green = parts[1]; blue = parts[2]
            } else if let white = parts.first {
                red = white; green = white; blue = white
            }
        }
        self.init(r: RGB.clamp(red), g: RGB.clamp(green), b: RGB.clamp(blue))
    }

    var uiColor: UIColor { uiColor(alpha: 1) }

    func uiColor(alpha: CGFloat) -> UIColor {
        UIColor(red: r, green: g, blue: b, alpha: alpha)
    }

    func isClose(to other: RGB, tolerance: CGFloat = 1.0 / 255.0) -> Bool {
        abs(r - other.r) <= tolerance && abs(g - other.g) <= tolerance && abs(b - other.b) <= tolerance
    }

    private static func clamp(_ value: CGFloat) -> CGFloat {
        value.isFinite ? min(max(value, 0), 1) : 0
    }
}

/// One page of the open note: its ink, in display space of that page.
struct NotePage: Identifiable {
    let id: UUID
    var drawing: PKDrawing

    init(id: UUID = UUID(), drawing: PKDrawing = PKDrawing()) {
        self.id = id
        self.drawing = drawing
    }
}

struct NoteItem: Identifiable, Hashable, Sendable {
    let url: URL                      // standardized, symlinks resolved (pitfall P12)
    var id: URL { url }
    var title: String                 // file name without .pdf
    var modified: Date
    var created: Date
    var isFavourite: Bool
    var folderPath: String            // relative to the library root, "" for the root
}

struct FolderItem: Identifiable, Hashable, Sendable {
    let url: URL
    var id: URL { url }
    var name: String
    var noteCount: Int                // notes directly inside
}

struct TrashedItem: Identifiable, Hashable, Codable, Sendable {
    let id: UUID
    var title: String
    var originalPath: String          // relative to the library root
    var deletedAt: Date
    var isFolder: Bool
}

enum LibrarySort: String, CaseIterable, Codable, Sendable { case modified, created, name }

/// meta.json. Each field has one writer (section 4.6): NoteDocument writes template, lastPage and noteID;
/// the library writes isFavourite and created. Every write goes through MetadataStore.update.
struct NoteMetadata: Codable, Equatable, Sendable {
    var isFavourite: Bool = false
    var template: PaperTemplate = .standard
    var created: Date? = nil
    var lastPage: Int = 0
    var noteID: String? = nil         // the /ZenithNoteID of the PDF this sidecar belongs to

    private enum CodingKeys: String, CodingKey { case isFavourite, template, created, lastPage, noteID }
}

extension NoteMetadata {
    /// Every field is optional on disk, so a meta.json written by an older build, or by only one of its
    /// writers, still decodes; a missing field keeps its default.
    init(from decoder: Decoder) throws {
        self.init()
        let container = try decoder.container(keyedBy: CodingKeys.self)
        isFavourite = try container.decodeIfPresent(Bool.self, forKey: .isFavourite) ?? isFavourite
        template = try container.decodeIfPresent(PaperTemplate.self, forKey: .template) ?? template
        created = try container.decodeIfPresent(Date.self, forKey: .created)
        lastPage = try container.decodeIfPresent(Int.self, forKey: .lastPage) ?? lastPage
        noteID = try container.decodeIfPresent(String.self, forKey: .noteID)
    }
}

enum TextFont: String, Codable, CaseIterable, Sendable {
    case helvetica, times, courier

    /// "Helvetica-BoldOblique", "Times-Italic", "Courier-Bold", ...: the PDF base font names.
    func postScriptName(bold: Bool, italic: Bool) -> String {
        switch self {
        case .helvetica: return "Helvetica" + TextFont.suffix(bold: bold, slanted: italic, slant: "Oblique", plain: "")
        case .times: return "Times" + TextFont.suffix(bold: bold, slanted: italic, slant: "Italic", plain: "-Roman")
        case .courier: return "Courier" + TextFont.suffix(bold: bold, slanted: italic, slant: "Oblique", plain: "")
        }
    }

    /// The installed face that stands in on the iPad when the base font name is not installed (Times).
    fileprivate func installedName(bold: Bool, italic: Bool) -> String? {
        guard self == .times else { return nil }
        switch (bold, italic) {
        case (true, true): return "TimesNewRomanPS-BoldItalicMT"
        case (true, false): return "TimesNewRomanPS-BoldMT"
        case (false, true): return "TimesNewRomanPS-ItalicMT"
        case (false, false): return "TimesNewRomanPSMT"
        }
    }

    private static func suffix(bold: Bool, slanted: Bool, slant: String, plain: String) -> String {
        switch (bold, slanted) {
        case (true, true): return "-Bold" + slant
        case (true, false): return "-Bold"
        case (false, true): return "-" + slant
        case (false, false): return plain
        }
    }
}

struct TextBoxStyle: Codable, Equatable, Sendable {
    var font: TextFont = .helvetica
    var size: CGFloat = 14
    var color: RGB = .black
    var bold: Bool = false
    var italic: Bool = false
    var alignment: Int = 0            // NSTextAlignment raw value: 0 left, 1 center, 2 right

    var uiFont: UIFont {
        if let face = UIFont(name: font.postScriptName(bold: bold, italic: italic), size: size) { return face }
        if let name = font.installedName(bold: bold, italic: italic), let face = UIFont(name: name, size: size) {
            return face
        }
        return .systemFont(ofSize: size)
    }

    private enum CodingKeys: String, CodingKey { case font, size, color, bold, italic, alignment }
}

extension TextBoxStyle {
    /// Missing fields keep their defaults, so a style saved by an older build still loads.
    init(from decoder: Decoder) throws {
        self.init()
        let container = try decoder.container(keyedBy: CodingKeys.self)
        font = try container.decodeIfPresent(TextFont.self, forKey: .font) ?? font
        size = try container.decodeIfPresent(CGFloat.self, forKey: .size) ?? size
        color = try container.decodeIfPresent(RGB.self, forKey: .color) ?? color
        bold = try container.decodeIfPresent(Bool.self, forKey: .bold) ?? bold
        italic = try container.decodeIfPresent(Bool.self, forKey: .italic) ?? italic
        alignment = try container.decodeIfPresent(Int.self, forKey: .alignment) ?? alignment
    }
}
