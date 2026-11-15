import PDFKit
import PencilKit
import XCTest
@testable import ZenithPDF

final class AppTests: XCTestCase {
    // MARK: - Demo content

    @MainActor func testDemoContentForEveryScene() async throws {
        for scene in DemoScene.allCases {
            let state = try DemoContent.prepare(scene)
            XCTAssertTrue(FileStore.exists(state.root), "\(scene) has no root")
            if let note = state.openNote {
                XCTAssertTrue(FileStore.exists(note), "\(scene) opens a missing note")
                XCTAssertTrue(SidecarLayout.isInside(note, root: state.root), "\(scene) opens a note outside its root")
            }
        }

        let empty = try DemoContent.prepare(.empty)
        XCTAssertEqual(try LibraryStore(root: empty.root).notes(in: nil, recursive: true).count, 0)

        let state = try DemoContent.prepare(.library)
        let store = LibraryStore(root: state.root)
        XCTAssertEqual(try store.folders(in: nil).map(\.name), ["History", "Physics", "Recipes"])
        let notes = try store.notes(in: nil, recursive: true)
        XCTAssertGreaterThanOrEqual(notes.count, 8)
        XCTAssertEqual(try store.favourites().count, 1)
        XCTAssertEqual(try TrashStore(library: store).items().count, 2)

        // Every template pattern, paper colour and size is used by some note.
        let templates = notes.map { MetadataStore.load(forNote: $0.url, libraryRoot: state.root).template }
        XCTAssertEqual(Set(templates.map(\.pattern)), Set(PaperPattern.allCases))
        XCTAssertEqual(Set(templates.map(\.color)), Set(PaperColor.allCases))
        XCTAssertEqual(Set(templates.map(\.size)), Set(PaperSize.allCases))

        // Every note opens and holds ink; the canned index matches the ink as it reads back, so no Vision runs.
        for note in notes {
            let document = try await NoteDocument.open(note.url, libraryRoot: state.root)
            XCTAssertTrue(document.pages.contains { !$0.drawing.strokes.isEmpty }, "\(note.title) has no ink")
            if note.title != "Desktop Ink" {
                let index = try XCTUnwrap(SearchIndex.load(note: note.url, libraryRoot: state.root), note.title)
                XCTAssertEqual(index.pages.count, document.pages.count)
                for (page, text) in zip(document.pages, index.pages) {
                    XCTAssertEqual(text.inkHash, FileStore.sha256(page.drawing.dataRepresentation()), note.title)
                }
            }
            await document.close()
        }

        // The note scenes: the lecture with text, a picture, a recording and twenty pages.
        let lectureState = try DemoContent.prepare(.note)
        let lectureURL = try XCTUnwrap(lectureState.openNote)
        let lecture = try await NoteDocument.open(lectureURL, libraryRoot: lectureState.root)
        XCTAssertGreaterThanOrEqual(lecture.pages.count, 20)
        let annotations = lecture.pdf.page(at: 0)?.annotations ?? []
        XCTAssertTrue(annotations.contains { TextBoxCodec.isTextBox($0) })
        XCTAssertTrue(annotations.contains { $0 is ImageAnnotation })
        let firstInk = lecture.pages.first?.drawing.strokes ?? []
        XCTAssertTrue(firstInk.contains { $0.ink.inkType == .marker })
        let recordings = RecordingStore.load(note: lectureURL, libraryRoot: lectureState.root)
        let recording = try XCTUnwrap(recordings.first)
        XCTAssertTrue(FileStore.exists(RecordingStore.fileURL(for: recording, note: lectureURL,
                                                              libraryRoot: lectureState.root)))
        // At the audio scene's replay time some strokes are already written and some are still to come.
        let timeline = ReplayTimeline(recording: recording)
        let offsets = firstInk.compactMap { timeline.offset(of: $0) }
        let audio = try DemoContent.prepare(.audio)
        let replayAt = try XCTUnwrap(audio.editor.replayAt)
        XCTAssertTrue(offsets.contains { $0 < replayAt })
        XCTAssertTrue(offsets.contains { $0 > replayAt })
        await lecture.close()

        // The search scene finds Newton in handwriting.
        let search = try DemoContent.prepare(.search)
        XCTAssertEqual(search.searchText, "Newton")
        let results = await LibrarySearch.search(search.searchText, in: notes, libraryRoot: search.root)
        XCTAssertTrue(results.contains { $0.hits.contains { $0.inInk } })

        XCTAssertEqual(try DemoContent.prepare(.trash).selection, .recentlyDeleted)
        XCTAssertEqual(try DemoContent.prepare(.templates).sheet, .newNote)
        XCTAssertEqual(try DemoContent.prepare(.settings).sheet, .settings)
        XCTAssertEqual(try DemoContent.prepare(.zoomed).editor.zoom, 2.5)
        XCTAssertTrue(try DemoContent.prepare(.pages).editor.showsPages)
        XCTAssertEqual(try DemoContent.prepare(.noteSearch).editor.searchQuery, "Newton")
    }

    @MainActor func testDemoBuiltOnce() throws {
        let state = try DemoContent.prepare(.library)
        let sentinel = state.root.appendingPathComponent("sentinel", isDirectory: false)
        try Data("kept".utf8).write(to: sentinel)
        defer { try? FileManager.default.removeItem(at: sentinel) }

        _ = try DemoContent.prepare(.note)
        XCTAssertTrue(FileStore.exists(sentinel), "the demo was built again although its version matched")

        let version = state.root.appendingPathComponent("version", isDirectory: false)
        try Data("0".utf8).write(to: version)
        _ = try DemoContent.prepare(.library)
        XCTAssertFalse(FileStore.exists(sentinel), "an older demo was not rebuilt")
        XCTAssertEqual(try String(contentsOf: version, encoding: .utf8), String(DemoContent.version))

        // The empty scene starts empty at every launch.
        let empty = try DemoContent.prepare(.empty)
        try Data("x".utf8).write(to: empty.root.appendingPathComponent("Leftover.pdf"))
        let again = try DemoContent.prepare(.empty)
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: again.root.path), [])
    }

    @MainActor func testDemoInkDrawsWords() {
        let origin = CGPoint(x: 60, y: 160)
        let strokes = DemoInk.strokes("Zenith notes", at: origin, size: 56, ink: PKInk(.pen, color: .black),
                                      startingAt: Date(timeIntervalSince1970: 1000), secondsPerStroke: 0.5)
        let words = DemoInk.words("Zenith notes", at: origin, size: 56)
        XCTAssertGreaterThan(strokes.count, 10)
        XCTAssertEqual(words.map(\.text), ["Zenith", "notes"])
        guard strokes.count > 1, words.count == 2 else { return }
        XCTAssertLessThan(words[0].rect.maxX, words[1].rect.minX + 1)
        // Every point lies inside the union of the word rects, grown by the pen width.
        let area = words.map(\.rect).reduce(CGRect.null) { $0.union($1) }.insetBy(dx: -4, dy: -4)
        for stroke in strokes {
            for point in stroke.path {
                XCTAssertTrue(area.contains(point.location), "\(point.location) outside \(area)")
            }
        }
        // Strokes are dated one after another.
        XCTAssertEqual(strokes[0].path.creationDate, Date(timeIntervalSince1970: 1000))
        XCTAssertEqual(strokes[1].path.creationDate, Date(timeIntervalSince1970: 1000.5))
    }

    // MARK: - Registry

    @MainActor func testRegistryRefusesSecondOpen() async throws {
        let folder = TestSupport.temporaryFolder()
        let url = TestSupport.writeNote(TestSupport.pdf(pages: 1), named: "Twice", in: folder)
        let registry = NoteRegistry()
        let document = try await registry.open(url, libraryRoot: folder)
        XCTAssertTrue(registry.isOpen(url))
        // The same file under another spelling is the same note.
        let other = URL(fileURLWithPath: folder.path + "/./Twice.pdf")
        do {
            _ = try await registry.open(other, libraryRoot: folder)
            XCTFail("a second open succeeded")
        } catch let error as ZenithError {
            XCTAssertEqual(error, .cannotOpen(other))
        }
        await registry.close(document)
        XCTAssertFalse(registry.isOpen(url))
        let reopened = try await registry.open(url, libraryRoot: folder)
        await registry.close(reopened)
    }

    @MainActor func testRegistryReopenWaitsForClose() async throws {
        let folder = TestSupport.temporaryFolder()
        let url = TestSupport.writeNote(TestSupport.pdf(pages: 1), named: "Reopen", in: folder)
        let registry = NoteRegistry()
        let document = try await registry.open(url, libraryRoot: folder)
        let line = TestSupport.stroke([CGPoint(x: 60, y: 100), CGPoint(x: 200, y: 120), CGPoint(x: 320, y: 100)])
        document.setDrawing(PKDrawing(strokes: [line]), forPageID: document.pageID(at: 0), undoable: true)
        // Not awaited: going back and tapping the note again at once must still read what the close saves.
        Task { await registry.close(document) }
        // The close has begun (the note is no longer listed) but its save may still be running.
        while registry.isOpen(url) { await Task.yield() }
        let reopened = try await registry.open(url, libraryRoot: folder)
        XCTAssertEqual(reopened.pages.first?.drawing.strokes.count, 1)
        await registry.close(reopened)
    }

    @MainActor func testSaveAllSavesEveryOpenNote() async throws {
        let folder = TestSupport.temporaryFolder()
        let first = TestSupport.writeNote(TestSupport.pdf(pages: 1), named: "One", in: folder)
        let second = TestSupport.writeNote(TestSupport.pdf(pages: 1), named: "Two", in: folder)
        let registry = NoteRegistry()
        let one = try await registry.open(first, libraryRoot: folder)
        let two = try await registry.open(second, libraryRoot: folder)
        let documents = [one, two]
        for document in documents {
            let line = TestSupport.stroke([CGPoint(x: 50, y: 50), CGPoint(x: 150, y: 80)])
            document.setDrawing(PKDrawing(strokes: [line]), forPageID: document.pageID(at: 0), undoable: true)
        }
        await registry.saveAll()
        for document in documents {
            XCTAssertFalse(document.isDirty, document.title)
            XCTAssertEqual(PDFRaw.annotations(inFile: document.url, pageIndex: 0).filter { $0.subtype == "Ink" }.count, 1)
            await registry.close(document)
        }
    }

    // MARK: - Opening files

    @MainActor func testOpenURLMovesInboxFile() throws {
        let root = TestSupport.temporaryFolder()
        let suite = "AppTests-" + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let location = LibraryLocation(defaults: defaults)
        location.useTemporaryRoot(root)
        let model = LibraryModel(location: location)

        let inbox = location.localRoot.appendingPathComponent("Inbox", isDirectory: true)
        try FileStore.createFolder(inbox)
        let name = "Shared " + UUID().uuidString
        let incoming = TestSupport.writeNote(TestSupport.pdf(pages: 2), named: name, in: inbox)
        defer { try? FileManager.default.removeItem(at: incoming) }

        let opened = try XCTUnwrap(OpenURLHandler.handle(incoming, library: model))
        XCTAssertFalse(FileStore.exists(incoming))
        XCTAssertTrue(FileStore.exists(opened))
        XCTAssertEqual(opened.lastPathComponent, name + ".pdf")
        XCTAssertTrue(SidecarLayout.isInside(opened, root: root))
        XCTAssertEqual(PDFDocument(url: opened)?.pageCount, 2)

        // A second file of the same name does not overwrite the first.
        let again = TestSupport.writeNote(TestSupport.pdf(pages: 1), named: name, in: inbox)
        defer { try? FileManager.default.removeItem(at: again) }
        let second = try XCTUnwrap(OpenURLHandler.handle(again, library: model))
        XCTAssertNotEqual(second.lastPathComponent, opened.lastPathComponent)
        XCTAssertEqual(PDFDocument(url: opened)?.pageCount, 2)
    }

    @MainActor func testOpenURLInPlace() throws {
        let root = TestSupport.temporaryFolder()
        let elsewhere = TestSupport.temporaryFolder("Elsewhere")
        let suite = "AppTests-" + UUID().uuidString
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let location = LibraryLocation(defaults: defaults)
        location.useTemporaryRoot(root)
        let model = LibraryModel(location: location)

        let outside = TestSupport.writeNote(TestSupport.pdf(pages: 1), named: "From Files", in: elsewhere)
        let opened = try XCTUnwrap(OpenURLHandler.handle(outside, library: model))
        XCTAssertEqual(opened.standardizedFileURL.path, outside.standardizedFileURL.path)
        XCTAssertTrue(FileStore.exists(outside), "a file opened in place was moved")

        let inside = TestSupport.writeNote(TestSupport.pdf(pages: 1), named: "Mine", in: root)
        XCTAssertEqual(OpenURLHandler.handle(inside, library: model)?.standardizedFileURL.path,
                       inside.standardizedFileURL.path)

        let text = elsewhere.appendingPathComponent("notes.txt", isDirectory: false)
        try Data("text".utf8).write(to: text)
        XCTAssertNil(OpenURLHandler.handle(text, library: model))
        XCTAssertNil(OpenURLHandler.handle(try XCTUnwrap(URL(string: "https://example.com/a.pdf")), library: model))
    }

    // MARK: - Launch options and windows

    func testLaunchOptionsParse() {
        let defaults = UserDefaults.standard
        defer {
            defaults.removeObject(forKey: "ZenithDemo")
            defaults.removeObject(forKey: "ZenithFingerDrawing")
        }
        defaults.removeObject(forKey: "ZenithDemo")
        defaults.removeObject(forKey: "ZenithFingerDrawing")
        XCTAssertNil(LaunchOptions.demoScene)
        XCTAssertNil(LaunchOptions.fingerDrawing)

        defaults.set("zoomed", forKey: "ZenithDemo")
        XCTAssertEqual(LaunchOptions.demoScene, .zoomed)
        defaults.set("noteSearch", forKey: "ZenithDemo")
        XCTAssertEqual(LaunchOptions.demoScene, .noteSearch)
        defaults.set("nonsense", forKey: "ZenithDemo")
        XCTAssertNil(LaunchOptions.demoScene)

        // What -ZenithFingerDrawing YES and NO put in the argument domain.
        defaults.set("YES", forKey: "ZenithFingerDrawing")
        XCTAssertEqual(LaunchOptions.fingerDrawing, true)
        XCTAssertEqual(LaunchOptions.baseEditorLaunch().fingerDrawing, true)
        defaults.set("NO", forKey: "ZenithFingerDrawing")
        XCTAssertEqual(LaunchOptions.fingerDrawing, false)

        XCTAssertTrue(LaunchOptions.isUnitTesting)
    }

    func testNoteWindowValueResolves() throws {
        let root = TestSupport.temporaryFolder()
        let note = TestSupport.writeNote(TestSupport.pdf(pages: 1), named: "Window",
                                         in: root.appendingPathComponent("Physics", isDirectory: true))
        let inLibrary = NoteWindowValue(url: note, libraryRoot: root)
        XCTAssertEqual(inLibrary.libraryPath, "Physics/Window.pdf")
        XCTAssertNil(inLibrary.bookmark)
        // Restored from its saved form, as a relaunch does.
        let restored = try JSONDecoder().decode(NoteWindowValue.self, from: JSONEncoder().encode(inLibrary))
        XCTAssertEqual(restored, inLibrary)
        let resolved = try XCTUnwrap(restored.resolve(libraryRoot: root))
        XCTAssertTrue(SidecarLayout.isInside(resolved, root: note) && SidecarLayout.isInside(note, root: resolved))
        XCTAssertNil(restored.resolve(libraryRoot: nil))

        let elsewhere = TestSupport.temporaryFolder("ElsewhereWindow")
        let outside = TestSupport.writeNote(TestSupport.pdf(pages: 1), named: "Outside", in: elsewhere)
        let inPlace = NoteWindowValue(url: outside, libraryRoot: root)
        XCTAssertNil(inPlace.libraryPath)
        XCTAssertNotNil(inPlace.bookmark)
        let found = try XCTUnwrap(inPlace.resolve(libraryRoot: root))
        XCTAssertTrue(SidecarLayout.isInside(found, root: outside) && SidecarLayout.isInside(outside, root: found))

        try FileManager.default.removeItem(at: note)
        XCTAssertNil(inLibrary.resolve(libraryRoot: root), "a deleted note resolved")
    }

    // MARK: - Ready markers

    @MainActor func testReadyMarkerWritten() async throws {
        let caches = try XCTUnwrap(FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first)

        // Waiting for both the shell and the content: neither alone writes the marker.
        let gated = "unit-gated-" + UUID().uuidString
        let gatedFile = caches.appendingPathComponent("ready-" + gated, isDirectory: false)
        defer { try? FileManager.default.removeItem(at: gatedFile) }
        LaunchOptions.expectReady(gated, waitsForContent: true, presentation: false)
        LaunchOptions.markShellReady()
        try await Task.sleep(nanoseconds: 500_000_000)
        XCTAssertFalse(FileStore.exists(gatedFile), "written before the content was drawn")
        LaunchOptions.markReady(gated)
        try await waitForFile(gatedFile)

        // Once the launch's marker is written, other calls write their own marker directly.
        let direct = "unit-direct-" + UUID().uuidString
        let directFile = caches.appendingPathComponent("ready-" + direct, isDirectory: false)
        defer { try? FileManager.default.removeItem(at: directFile) }
        LaunchOptions.markReady(direct)
        try await waitForFile(directFile)
    }

    /// Polls for a file the main actor writes asynchronously; 30 s is a timeout, not a measured duration.
    @MainActor private func waitForFile(_ url: URL, file: StaticString = #filePath, line: UInt = #line) async throws {
        for _ in 0..<300 {
            if FileStore.exists(url) { return }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        XCTFail("\(url.lastPathComponent) was never written", file: file, line: line)
    }
}
