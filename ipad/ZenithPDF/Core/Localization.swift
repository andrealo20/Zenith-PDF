import Foundation

/// A string built from a format: tr("Page %lld of %lld", n, m). The app is in English only, so the key is the text.
func tr(_ key: String, _ arguments: CVarArg...) -> String {
    let format = Bundle.main.localizedString(forKey: key, value: key, table: nil)
    guard !arguments.isEmpty else { return format }
    return String(format: format, locale: Locale.current, arguments: arguments)
}
