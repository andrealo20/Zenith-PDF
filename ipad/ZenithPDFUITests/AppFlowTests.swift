import XCTest

/// Five flows through the real UI, on the demo library. The editor is found by its accessibility identifier
/// "editor", whose value is "strokes: N".
final class AppFlowTests: XCTestCase {
    /// Generous waits: a cold simulator can take long to build the demo and draw; these never measure speed.
    private let wait: TimeInterval = 60

    private struct FlowError: Error, CustomStringConvertible {
        let description: String
    }

    /// (1) A drag across the page adds one stroke.
    @MainActor func testDrawingAddsStroke() throws {
        let app = launch(scene: "note", fingerDrawing: true)
        let editor = try waitForEditor(app)
        let before = try strokeCount(editor)
        drag(on: editor, from: CGVector(dx: 0.3, dy: 0.45), to: CGVector(dx: 0.6, dy: 0.5))
        try waitForStrokes(before + 1, in: editor)
    }

    /// (2) Swipe through twenty pages and pinch to zoom without a crash; one-finger swipes never draw.
    @MainActor func testScrollAndZoomTwentyPages() throws {
        let app = launch(scene: "note", fingerDrawing: false)
        let editor = try waitForEditor(app)
        let before = try strokeCount(editor)
        for _ in 0..<30 { editor.swipeUp(velocity: .fast) }
        editor.pinch(withScale: 3, velocity: 2)
        for _ in 0..<5 { editor.swipeDown(velocity: .fast) }
        editor.pinch(withScale: 0.4, velocity: -2)
        XCTAssertEqual(app.state, .runningForeground)
        XCTAssertTrue(editor.exists)
        XCTAssertEqual(try strokeCount(editor), before)
    }

    /// (3) In the empty library: create a note, write, go back, reopen; the stroke is there.
    @MainActor func testCreateWriteReopen() throws {
        let app = launch(scene: "empty", fingerDrawing: true)
        try createNote(in: app)
        let editor = try waitForEditor(app)
        XCTAssertEqual(try strokeCount(editor), 0)
        drag(on: editor, from: CGVector(dx: 0.3, dy: 0.4), to: CGVector(dx: 0.65, dy: 0.45))
        try waitForStrokes(1, in: editor)
        try tap(app.buttons["Back"], "Back")

        try waitForNote(named: "Untitled", in: app).tap()
        let reopened = try waitForEditor(app)
        try waitForStrokes(1, in: reopened)
    }

    /// (4) Switch to Text and back to Write: the tool picker is visible again and writing works.
    @MainActor func testToolPickerBackAfterText() throws {
        let app = launch(scene: "note", fingerDrawing: true)
        let editor = try waitForEditor(app)
        let picker = toolPicker(in: app)
        XCTAssertTrue(picker.waitForExistence(timeout: wait), "no tool picker in write mode")

        try tap(app.buttons["Text"], "Text")
        XCTAssertTrue(waitForDisappearance(of: picker), "the tool picker stayed in text mode")

        try tap(app.buttons["Write"], "Write")
        XCTAssertTrue(picker.waitForExistence(timeout: wait), "the tool picker did not come back")
        let before = try strokeCount(editor)
        drag(on: editor, from: CGVector(dx: 0.3, dy: 0.6), to: CGVector(dx: 0.6, dy: 0.62))
        try waitForStrokes(before + 1, in: editor)
    }

    /// (5) Delete a note and restore it from Recently Deleted.
    @MainActor func testDeleteAndRestore() throws {
        let app = launch(scene: "empty", fingerDrawing: false)
        try createNote(in: app)
        _ = try waitForEditor(app)
        try tap(app.buttons["Back"], "Back")

        let tile = try waitForNote(named: "Untitled", in: app)
        tile.press(forDuration: 1.2)
        try tap(app.buttons["Delete"], "Delete")
        // Some lists ask again before deleting.
        let confirm = app.sheets.buttons["Delete"]
        if confirm.waitForExistence(timeout: 2) { confirm.tap() }
        XCTAssertTrue(waitForDisappearance(of: tile), "the deleted note is still in the library")

        try showSidebarItem("Recently Deleted", in: app)
        let trashed = try waitForNote(named: "Untitled", in: app)
        if !app.buttons["Restore"].waitForExistence(timeout: 3) {
            trashed.press(forDuration: 1.2)
        }
        try tap(app.buttons["Restore"], "Restore")

        try showSidebarItem("All Notes", in: app)
        _ = try waitForNote(named: "Untitled", in: app)
    }

    // MARK: - Helpers

    @MainActor private func launch(scene: String, fingerDrawing: Bool) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["-ZenithDemo", scene, "-ZenithFingerDrawing", fingerDrawing ? "YES" : "NO"]
        app.launch()
        return app
    }

    @MainActor private func waitForEditor(_ app: XCUIApplication) throws -> XCUIElement {
        let editor = app.descendants(matching: .any).matching(identifier: "editor").firstMatch
        guard editor.waitForExistence(timeout: wait) else { throw FlowError(description: "no editor on screen") }
        // The value appears once the pages are loaded.
        let counted = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value BEGINSWITH 'strokes: '"),
                                                object: editor)
        guard XCTWaiter().wait(for: [counted], timeout: wait) == .completed else {
            throw FlowError(description: "the editor never counted its strokes")
        }
        return editor
    }

    @MainActor private func strokeCount(_ editor: XCUIElement) throws -> Int {
        let value = (editor.value as? String) ?? ""
        guard value.hasPrefix("strokes: "), let count = Int(value.dropFirst("strokes: ".count)) else {
            throw FlowError(description: "unexpected editor value \"\(value)\"")
        }
        return count
    }

    @MainActor private func waitForStrokes(_ count: Int, in editor: XCUIElement) throws {
        let reached = XCTNSPredicateExpectation(predicate: NSPredicate(format: "value == %@", "strokes: \(count)"),
                                                object: editor)
        guard XCTWaiter().wait(for: [reached], timeout: wait) == .completed else {
            throw FlowError(description: "expected strokes: \(count), the editor says \((editor.value as? String) ?? "nothing")")
        }
    }

    @MainActor private func drag(on element: XCUIElement, from start: CGVector, to end: CGVector) {
        let from = element.coordinate(withNormalizedOffset: start)
        let to = element.coordinate(withNormalizedOffset: end)
        from.press(forDuration: 0.1, thenDragTo: to, withVelocity: .slow, thenHoldForDuration: 0.1)
    }

    @MainActor private func tap(_ element: XCUIElement, _ name: String) throws {
        guard element.waitForExistence(timeout: wait) else { throw FlowError(description: "no \(name) on screen") }
        element.tap()
    }

    /// The first of `elements` to appear.
    @MainActor private func firstToAppear(_ elements: [XCUIElement], _ name: String) throws -> XCUIElement {
        for _ in 0..<Int(wait) {
            if let found = elements.first(where: { $0.exists }) { return found }
            _ = elements.first?.waitForExistence(timeout: 1)
        }
        throw FlowError(description: "no \(name) on screen")
    }

    /// New, then its Note item, then Create in the template sheet when one is shown.
    @MainActor private func createNote(in app: XCUIApplication) throws {
        try tap(app.buttons["New"], "New")
        try firstToAppear([app.buttons["New Note"], app.buttons["Note"]], "New Note").tap()
        let create = app.buttons["Create"]
        if create.waitForExistence(timeout: 10) { create.tap() }
    }

    @MainActor private func waitForNote(named title: String, in app: XCUIApplication) throws -> XCUIElement {
        let tile = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", title)).firstMatch
        guard tile.waitForExistence(timeout: wait) else { throw FlowError(description: "no note \"\(title)\" on screen") }
        return tile
    }

    /// A sidebar row; in portrait the sidebar is hidden behind its toggle first.
    @MainActor private func showSidebarItem(_ label: String, in app: XCUIApplication) throws {
        let row = app.descendants(matching: .any).matching(NSPredicate(format: "label == %@", label)).firstMatch
        if !(row.waitForExistence(timeout: 3) && row.isHittable) {
            let toggle = try firstToAppear([app.buttons["Show Sidebar"], app.buttons["ToggleSidebar"]], "sidebar button")
            toggle.tap()
        }
        try tap(row, label)
    }

    /// Any of the PencilKit tool picker's tools.
    @MainActor private func toolPicker(in app: XCUIApplication) -> XCUIElement {
        let tools = NSPredicate(format: "label CONTAINS[c] 'eraser' OR label CONTAINS[c] 'lasso'")
        return app.descendants(matching: .any).matching(tools).firstMatch
    }

    @MainActor private func waitForDisappearance(of element: XCUIElement) -> Bool {
        let gone = XCTNSPredicateExpectation(predicate: NSPredicate(format: "exists == false"), object: element)
        return XCTWaiter().wait(for: [gone], timeout: wait) == .completed
    }
}
