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

    /// Section 2.2 values. The loops are the desktop's (core/pages.py template_content), which works in PDF
    /// space from the bottom; positions are turned to top-left coordinates afterwards so the counts match exactly.
    static func marks(for pattern: PaperPattern, size: CGSize) -> TemplateMarks {
        let mm = millimetre
        let width = size.width, height = size.height
        var marks = TemplateMarks(rules: [], ruleWidth: 0, dots: [], dotDiameter: 0, margin: nil, marginWidth: 0)
        switch pattern {
        case .blank:
            break
        case .lined, .collegeRuled:
            let spacing: CGFloat = pattern == .lined ? 8 * mm : 20.25
            var y = height - 25 * mm
            while y > 12 * mm {
                marks.rules.append(RuleSegment(start: CGPoint(x: 12 * mm, y: height - y),
                                               end: CGPoint(x: width - 12 * mm, y: height - y)))
                y -= spacing
            }
            marks.ruleWidth = 0.5
            marks.margin = RuleSegment(start: CGPoint(x: 25 * mm, y: 0), end: CGPoint(x: 25 * mm, y: height))
            marks.marginWidth = 0.6
        case .grid:
            let spacing = 5 * mm
            var x = spacing
            while x < width {
                marks.rules.append(RuleSegment(start: CGPoint(x: x, y: 0), end: CGPoint(x: x, y: height)))
                x += spacing
            }
            var y = spacing
            while y < height {
                marks.rules.append(RuleSegment(start: CGPoint(x: 0, y: height - y), end: CGPoint(x: width, y: height - y)))
                y += spacing
            }
            marks.ruleWidth = 0.35
        case .dotted:
            let spacing = 5 * mm
            var y = spacing
            while y < height {
                var x = spacing
                while x < width {
                    marks.dots.append(CGPoint(x: x, y: height - y))
                    x += spacing
                }
                y += spacing
            }
            marks.dotDiameter = 1.3
        }
        return marks
    }
}
