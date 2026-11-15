import SwiftUI
import UIKit

/// The desktop's Folio style (src/zenith_pdf/styles/folio.py) as dynamic colours: light, dark.
enum Folio {
    static let window = Color(uiColor: UI.window)
    static let surface = Color(uiColor: dynamic(light: 0xFBFBFC, dark: 0x22252B))
    static let raised = Color(uiColor: UI.raised)
    static let canvas = Color(uiColor: UI.canvas)
    static let border = Color(uiColor: dynamic(light: 0xD5DBE3, dark: 0x3B404B))
    static let text = Color(uiColor: UI.text)
    static let textMuted = Color(uiColor: UI.textMuted)
    static let textDisabled = Color(uiColor: dynamic(light: 0xAAB5C6, dark: 0x6B7280))
    static let accent = Color(uiColor: UI.accent)
    static let accentInk = Color(uiColor: dynamic(light: 0x2456D6, dark: 0x9DB8FF))
    static let selection = Color(uiColor: dynamic(light: 0x444F60, dark: 0xFFFFFF, lightAlpha: 0.12, darkAlpha: 0.10))
    static let danger = Color(uiColor: dynamic(light: 0xC8284F, dark: 0xFF6B81))
    static let paper = Color(uiColor: dynamic(light: 0xFCFAF4, dark: 0xFCFAF4))
    fileprivate static let shadow = Color(uiColor: dynamic(light: 0x171A20, dark: 0x000000, lightAlpha: 0.15, darkAlpha: 0.43))

    enum UI {
        static let window = Folio.dynamic(light: 0xF6F6F8, dark: 0x1C1E23)
        static let raised = Folio.dynamic(light: 0xFFFFFF, dark: 0x2B2E35)
        static let canvas = Folio.dynamic(light: 0xE8EBF0, dark: 0x131417)
        static let text = Folio.dynamic(light: 0x171A20, dark: 0xEEF0F4)
        static let textMuted = Folio.dynamic(light: 0x444F60, dark: 0xB3BAC7)
        static let accent = Folio.dynamic(light: 0x4278FF, dark: 0x6F97FF)
    }

    /// A colour that follows the appearance; `light` and `dark` are 0xRRGGBB.
    static func dynamic(light: UInt32, dark: UInt32, lightAlpha: CGFloat = 1, darkAlpha: CGFloat = 1) -> UIColor {
        UIColor { traits in
            traits.userInterfaceStyle == .dark ? Folio.color(dark, alpha: darkAlpha) : Folio.color(light, alpha: lightAlpha)
        }
    }

    private static func color(_ hex: UInt32, alpha: CGFloat) -> UIColor {
        UIColor(
            red: CGFloat((hex >> 16) & 0xFF) / 255,
            green: CGFloat((hex >> 8) & 0xFF) / 255,
            blue: CGFloat(hex & 0xFF) / 255,
            alpha: alpha
        )
    }
}

extension View {
    /// Raised fill, rounded rectangle, Folio shadow.
    func folioCard(radius: CGFloat = 10) -> some View {
        self.background(
            RoundedRectangle(cornerRadius: radius, style: .continuous)
                .fill(Folio.raised)
                .shadow(color: Folio.shadow, radius: 9, x: 0, y: 6)
        )
    }

    /// Bold serif large title in Folio.text.
    func folioTitle() -> some View {
        self.font(.system(.largeTitle, design: .serif).weight(.bold))
            .foregroundStyle(Folio.text)
    }
}
