import Foundation
import os

enum DemoScene: String, CaseIterable {
    case library, note, darkPaper, zoomed, pages, templates, search, noteSearch, trash, audio, settings, desktopNote, empty
}

/// -ZenithDemo, -ZenithFingerDrawing, ready markers, unit-test detection.
enum LaunchOptions {
    /// UserDefaults "ZenithDemo" (from -ZenithDemo <scene>).
    static var demoScene: DemoScene? {
        UserDefaults.standard.string(forKey: "ZenithDemo").flatMap { DemoScene(rawValue: $0) }
    }

    /// "ZenithFingerDrawing" (from -ZenithFingerDrawing YES), for UI tests.
    static var fingerDrawing: Bool? {
        guard UserDefaults.standard.object(forKey: "ZenithFingerDrawing") != nil else { return nil }
        return UserDefaults.standard.bool(forKey: "ZenithFingerDrawing")
    }

    /// NSClassFromString("XCTestCase") != nil
    static var isUnitTesting: Bool { NSClassFromString("XCTestCase") != nil }

    /// Writes Library/Caches/ready-<name> once the first screen of this launch has appeared and finished loading
    /// (the scene name, or "launch" without a demo scene). ci.sh waits for the file before a screenshot.
    static func markReady(_ name: String) {
        guard let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first else { return }
        do {
            try FileManager.default.createDirectory(at: caches, withIntermediateDirectories: true)
            try Data().write(to: caches.appendingPathComponent("ready-" + name, isDirectory: false))
        } catch {
            log.error("Ready marker \(name, privacy: .public) not written: \(error.localizedDescription, privacy: .public)")
        }
    }
}
