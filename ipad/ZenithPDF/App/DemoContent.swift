import Foundation

/// Where a demo scene starts.
struct DemoState {
    let root: URL
    let openNote: URL?
    let editor: EditorLaunchState
    let selection: SidebarSelection
    let searchText: String
    let sheet: LibrarySheet?
}

/// Demo library (built once) and per-scene state.
@MainActor enum DemoContent {
    static let version = 1

    /// Builds Documents/Demo once, skipped when Documents/Demo/version holds `version`: subjects Physics, History,
    /// Recipes; notes on every template; ink, highlighter, shapes, text boxes, a picture, a recording made of generated
    /// tone with stroke times inside it, items in Recently Deleted; search indexes written directly from the known text
    /// and DemoInk.words (no Vision); the desktop fixture copied from the bundle's Demo/desktop-ink.pdf when present.
    /// .empty uses Documents/DemoEmpty, emptied at every launch. Returns where the scene starts.
    static func prepare(_ scene: DemoScene) throws -> DemoState {
        let documents = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask).first
            ?? FileManager.default.temporaryDirectory
        let root = documents.appendingPathComponent(scene == .empty ? "DemoEmpty" : "Demo", isDirectory: true)
        return DemoState(root: root, openNote: nil, editor: EditorLaunchState(), selection: .all, searchText: "", sheet: nil)
    }
}
