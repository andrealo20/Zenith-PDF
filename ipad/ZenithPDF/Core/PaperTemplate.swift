import CoreGraphics

enum PaperPattern: String, Codable, CaseIterable, Sendable { case blank, lined, collegeRuled, grid, dotted }

enum PaperColor: String, Codable, CaseIterable, Sendable {
    case white, cream, dark

    var fill: RGB {
        switch self {
        case .white: return RGB(r: 1, g: 1, b: 1)
        case .cream: return RGB(r: 252.0 / 255.0, g: 250.0 / 255.0, b: 244.0 / 255.0)   // #FCFAF4
        case .dark: return RGB(r: 31.0 / 255.0, g: 34.0 / 255.0, b: 38.0 / 255.0)       // #1F2226
        }
    }

    var rule: RGB {
        self == .dark ? RGB(r: 0.32, g: 0.36, b: 0.43) : RGB(r: 0.72, g: 0.79, b: 0.89)
    }

    var margin: RGB {
        self == .dark ? RGB(r: 0.75, g: 0.38, b: 0.40) : RGB(r: 0.93, g: 0.55, b: 0.55)
    }

    var isDark: Bool { self == .dark }
}

enum PaperSize: String, Codable, CaseIterable, Sendable {
    case a4, letter

    /// Portrait.
    var points: CGSize {
        switch self {
        case .a4: return CGSize(width: 595.28, height: 841.89)
        case .letter: return CGSize(width: 612, height: 792)
        }
    }
}

struct PaperTemplate: Codable, Hashable, Sendable {
    var pattern: PaperPattern; var color: PaperColor; var size: PaperSize

    static let standard = PaperTemplate(pattern: .lined, color: .white, size: .a4)

    init(pattern: PaperPattern, color: PaperColor, size: PaperSize) {
        self.pattern = pattern
        self.color = color
        self.size = size
    }
}
