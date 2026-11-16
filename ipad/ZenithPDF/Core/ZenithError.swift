import Foundation
import os

let log = Logger(subsystem: "io.github.andrealo20.zenithpdf", category: "app")

enum ZenithError: LocalizedError, Equatable {
    case cannotOpen(URL)
    case cannotSave(URL, reason: String)
    case invalidPDF
    case locked(URL)                  // password-protected
    case nameTaken(String)
    case invalidName(String)
    case accessDenied(URL)
    case conflict(original: URL, copy: URL)

    var errorDescription: String? {
        switch self {
        case .cannotOpen, .invalidPDF:
            return tr("The note could not be opened.")
        case .cannotSave:
            return tr("The note could not be saved. Your changes are kept and saving will be tried again.")
        case .locked:
            return tr("This PDF is locked and opens read-only.")
        case .nameTaken:
            return tr("A note or folder with this name already exists.")
        case .invalidName:
            return tr("Names cannot be empty, start with a dot, or contain / or :.")
        case .accessDenied:
            return tr("Zenith PDF cannot open this folder.")
        case .conflict(_, let copy):
            return tr("The file changed on another device. Your changes were saved as \"%@\".",
                      copy.deletingPathExtension().lastPathComponent as NSString)
        }
    }
}
