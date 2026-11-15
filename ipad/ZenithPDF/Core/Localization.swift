import Foundation

/// The one way to show a translated string built from a format: tr("Page %lld of %lld", n, m).
/// Plain literals may also go straight into SwiftUI initialisers: Text("Library"). Never interpolate inside
/// a SwiftUI literal (make_strings.py --check reports it).
func tr(_ key: String, _ arguments: CVarArg...) -> String {
    let format = Bundle.main.localizedString(forKey: key, value: key, table: nil)
    guard !arguments.isEmpty else { return format }
    return String(format: format, locale: Locale.current, arguments: arguments)
}
