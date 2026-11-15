import CoreGraphics

struct RuleSegment: Equatable { var start: CGPoint; var end: CGPoint }   // top-left page coordinates

struct TemplateMarks: Equatable {
    var rules: [RuleSegment]; var ruleWidth: CGFloat
    var dots: [CGPoint]; var dotDiameter: CGFloat
    var margin: RuleSegment?; var marginWidth: CGFloat
}

/// Rule positions (pure).
enum TemplateGeometry {
    static let millimetre: CGFloat = 72 / 25.4

    /// Section 2.2 values.
    static func marks(for pattern: PaperPattern, size: CGSize) -> TemplateMarks {
        TemplateMarks(rules: [], ruleWidth: 0, dots: [], dotDiameter: 0, margin: nil, marginWidth: 0)
    }
}
