import Foundation
import os
import UIKit

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
    ///
    /// The library and the editor call this when their content is drawn. While the shell waits for this launch's
    /// first screen (expectReady), the call counts as "content drawn" and the marker is written once the shell is
    /// ready too and no sheet or cover is still animating in. Without an expectation it writes `name` directly.
    static func markReady(_ name: String) {
        Task { @MainActor in
            if var waiting = expectation, !waiting.done {
                waiting.contentReady = true
                expectation = waiting
                await writeIfReady()
            } else {
                await settleAndWrite(name, needsPresentation: false)
            }
        }
    }

    // MARK: - Launch, used by RootView, NoteScreen and the note window

    /// What this launch shows first, decided once: the demo library of the scene, or the person's library resolved
    /// from its bookmark. Every window calls it; only the first call does the work.
    @MainActor static func prepareLaunch() -> DemoState? {
        if launchPrepared { return launchDemo }
        launchPrepared = true
        guard let scene = demoScene else {
            LibraryLocation.shared.resolve()
            return nil
        }
        do {
            let state = try DemoContent.prepare(scene)
            LibraryLocation.shared.useTemporaryRoot(state.root)
            launchDemo = state
        } catch {
            // Never fall back to the person's library while a demo scene was asked for.
            log.error("Demo \(scene.rawValue, privacy: .public) not built: \(error.localizedDescription, privacy: .public)")
            let empty = FileManager.default.temporaryDirectory.appendingPathComponent("DemoFailed", isDirectory: true)
            try? FileStore.createFolder(empty)
            LibraryLocation.shared.useTemporaryRoot(empty)
        }
        return launchDemo
    }

    /// The editor settings every note opened in this launch starts with.
    static func baseEditorLaunch() -> EditorLaunchState {
        var launch = EditorLaunchState()
        launch.fingerDrawing = fingerDrawing
        return launch
    }

    /// The marker this launch writes, and what it waits for: the shell's own work (data prepared, sheet or note
    /// asked for) always; the content's markReady call when `waitsForContent`; a finished sheet or full-screen
    /// presentation when `presentation`.
    @MainActor static func expectReady(_ name: String, waitsForContent: Bool, presentation: Bool) {
        expectation = ReadyExpectation(name: name, waitsForContent: waitsForContent, presentation: presentation)
    }

    /// The shell has done its part for the expected marker.
    @MainActor static func markShellReady() {
        guard var waiting = expectation, !waiting.done else { return }
        waiting.shellReady = true
        expectation = waiting
        Task { @MainActor in await writeIfReady() }
    }

    // MARK: - Private

    private struct ReadyExpectation {
        let name: String
        let waitsForContent: Bool
        let presentation: Bool
        var shellReady = false
        var contentReady = false
        var done = false
    }

    @MainActor private static var launchPrepared = false
    @MainActor private static var launchDemo: DemoState? = nil
    @MainActor private static var expectation: ReadyExpectation? = nil
    @MainActor private static var written: Set<String> = []

    @MainActor private static func writeIfReady() async {
        guard var waiting = expectation, !waiting.done, waiting.shellReady,
              waiting.contentReady || !waiting.waitsForContent else { return }
        waiting.done = true
        expectation = waiting
        await settleAndWrite(waiting.name, needsPresentation: waiting.presentation)
    }

    /// Waits until no sheet or cover is moving (and one is up when `needsPresentation`), then writes the marker once.
    @MainActor private static func settleAndWrite(_ name: String, needsPresentation: Bool) async {
        // Up to 20 s, far more than a presentation takes; a scene that never settles still gets its marker.
        for _ in 0..<200 {
            if presentationSettled(needsPresentation: needsPresentation) { break }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        // One more frame, so what settled has been committed to the screen.
        try? await Task.sleep(nanoseconds: 100_000_000)
        guard !written.contains(name) else { return }
        written.insert(name)
        writeMarker(name)
    }

    @MainActor private static func presentationSettled(needsPresentation: Bool) -> Bool {
        let windows = UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows)
        guard let root = (windows.first { $0.isKeyWindow } ?? windows.first)?.rootViewController else {
            return !needsPresentation
        }
        var top = root
        var presented = false
        while let next = top.presentedViewController {
            if next.isBeingPresented || next.isBeingDismissed { return false }
            top = next
            presented = true
        }
        return presented || !needsPresentation
    }

    private static func writeMarker(_ name: String) {
        guard let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first else { return }
        do {
            try FileManager.default.createDirectory(at: caches, withIntermediateDirectories: true)
            try Data().write(to: caches.appendingPathComponent("ready-" + name, isDirectory: false))
            log.info("Ready: \(name, privacy: .public)")
        } catch {
            log.error("Ready marker \(name, privacy: .public) not written: \(error.localizedDescription, privacy: .public)")
        }
    }
}
