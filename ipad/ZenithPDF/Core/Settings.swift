/// UserDefaults keys. A missing value means the default written next to the key.
enum SettingKey {
    static let sort = "library.sort"                     // LibrarySort raw value, default "modified"
    static let newNoteTemplate = "library.template"      // JSON of PaperTemplate, default .standard
    static let libraryBookmark = "library.bookmark"      // Data
    static let libraryChoiceMade = "library.choiceMade"  // Bool, default false: the first-launch sheet shows until set
    static let fingerDrawing = "editor.fingerDrawing"    // Bool, default false
    static let smartShapes = "editor.smartShapes"        // Bool, default false
    static let textStyle = "editor.textStyle"            // JSON of TextBoxStyle
    static let replayInk = "audio.replayInk"             // Bool, default true
}
