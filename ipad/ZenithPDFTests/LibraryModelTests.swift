import PDFKit
import UIKit
import XCTest
@testable import ZenithPDF

final class LibraryModelTests: XCTestCase {
    private var root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
    private var suite = ""

    override func setUp() {
        super.setUp()
        root = TestSupport.temporaryFolder(name)
        suite = "zenith-library-tests-\(UUID().uuidString)"
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: root)
        UserDefaults(suiteName: suite)?.removePersistentDomain(forName: suite)
        super.tearDown()
    }

    // MARK: - Helpers

    private var defaults: UserDefaults { UserDefaults(suiteName: suite) ?? .standard }

    /// A model on the test folder, as the demo and tests use it: no first-launch choice asked.
    @MainActor private func makeModel() -> LibraryModel {
        let location = LibraryLocation(defaults: defaults)
        location.useTemporaryRoot(root)
        let model = LibraryModel(location: location, defaults: defaults)
        model.refresh()
        return model
    }

    private var store: LibraryStore { LibraryStore(root: root) }

    private func pdfData(pages: Int = 1) -> Data {
        TestSupport.pdf(pages: pages).dataRepresentation() ?? Data()
    }

    @discardableResult private func makeNote(_ name: String, in folder: URL? = nil) throws -> URL {
        try store.createNote(named: name, pdfData: pdfData(), template: nil, in: folder)
    }

    private func path(_ url: URL?) -> String {
        guard let url else { return "" }
        var path = url.standardizedFileURL.resolvingSymlinksInPath().path
        if path.hasPrefix("/private/") { path = String(path.dropFirst("/private".count)) }
        if path.count > 1 && path.hasSuffix("/") { path.removeLast() }
        return path
    }

    @MainActor private func titles(_ model: LibraryModel) -> [String] {
        model.notes.map(\.title)
    }

    @MainActor private func selectedPath(_ model: LibraryModel) -> String {
        path(model.currentFolder)
    }

    private func image(_ size: CGSize, _ color: UIColor) -> UIImage {
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            color.setFill()
            context.fill(CGRect(origin: .zero, size: size))
        }
    }

    // MARK: - Tests

    @MainActor func testSelectionRefreshesLists() throws {
        let physics = try store.createFolder(named: "Physics", in: nil)
        let waves = try store.createFolder(named: "Waves", in: physics)
        try makeNote("Mechanics", in: physics)
        try makeNote("Sound", in: waves)
        try makeNote("Shopping")
        let model = makeModel()

        XCTAssertEqual(model.subjects.map(\.name), ["Physics"])
        XCTAssertEqual(model.selection, .all)
        XCTAssertEqual(Set(titles(model)), ["Mechanics", "Sound", "Shopping"])
        XCTAssertTrue(model.folders.isEmpty)

        model.selection = .folder(model.subjects[0].url)
        XCTAssertEqual(model.folders.map(\.name), ["Waves"])
        XCTAssertEqual(titles(model), ["Mechanics"])
        XCTAssertNil(model.parentFolder, "a subject's parent is the library root")
        XCTAssertEqual(model.title, "Physics")

        model.selection = .folder(model.folders[0].url)
        XCTAssertEqual(titles(model), ["Sound"])
        XCTAssertEqual(path(model.parentFolder), path(physics))

        model.selection = .favourites
        XCTAssertTrue(model.notes.isEmpty)
        model.selection = .recentlyDeleted
        XCTAssertTrue(model.notes.isEmpty)
        XCTAssertTrue(model.folders.isEmpty)

        // A folder removed behind the model's back falls back to All Notes.
        model.selection = .folder(waves)
        try FileManager.default.removeItem(at: waves)
        model.refresh()
        XCTAssertEqual(model.selection, .all)
        XCTAssertEqual(Set(titles(model)), ["Mechanics", "Shopping"])
    }

    @MainActor func testCreateNoteInCurrentFolder() throws {
        let history = try store.createFolder(named: "History", in: nil)
        let model = makeModel()
        model.selection = .folder(model.subjects[0].url)

        let template = PaperTemplate(pattern: .grid, color: .dark, size: .letter)
        let rome = try XCTUnwrap(model.createNote(template: template, name: " Rome "))
        XCTAssertEqual(path(rome.deletingLastPathComponent()), path(history))
        XCTAssertEqual(rome.lastPathComponent, "Rome.pdf")
        XCTAssertEqual(titles(model), ["Rome"])
        XCTAssertEqual(MetadataStore.load(forNote: rome, libraryRoot: root).template, template)
        let page = try XCTUnwrap(PDFDocument(url: rome)?.page(at: 0))
        let size = page.bounds(for: .mediaBox).size
        XCTAssertEqual(size.width, 612, accuracy: 0.01)
        XCTAssertEqual(size.height, 792, accuracy: 0.01)

        let first = try XCTUnwrap(model.createNote(template: .standard, name: nil))
        let second = try XCTUnwrap(model.createNote(template: .standard, name: "   "))
        XCTAssertEqual(first.lastPathComponent, "Untitled.pdf")
        XCTAssertEqual(second.lastPathComponent, "Untitled 2.pdf")
        XCTAssertEqual(Set(titles(model)), ["Rome", "Untitled", "Untitled 2"])

        model.createSubject(named: "Ancient")
        XCTAssertEqual(model.folders.map(\.name), ["Ancient"])
        XCTAssertTrue(FileManager.default.fileExists(atPath: history.appendingPathComponent("Ancient").path))

        XCTAssertNil(model.createNote(template: .standard, name: ".hidden"))
        guard case .invalidName = model.error else { return XCTFail("expected invalidName, got \(String(describing: model.error))") }
        XCTAssertEqual(model.notes.count, 3)
    }

    @MainActor func testRenameDuplicateMoveDelete() throws {
        let a = try makeNote("A")
        let physics = try store.createFolder(named: "Physics", in: nil)
        try store.createFolder(named: "Waves", in: physics)
        let model = makeModel()

        model.rename(a, to: "B")
        XCTAssertEqual(titles(model), ["B"])
        let b = model.notes[0].url
        XCTAssertFalse(FileManager.default.fileExists(atPath: a.path))

        model.duplicate(b)
        XCTAssertEqual(Set(titles(model)), ["B", "B 2"])
        let copy = try XCTUnwrap(model.notes.first { $0.title == "B 2" }).url

        model.move(copy, to: physics)
        model.selection = .folder(model.subjects[0].url)
        XCTAssertEqual(titles(model), ["B 2"])

        // Renaming the selected folder keeps it selected under its new name, and a folder inside it too.
        model.rename(physics, to: "Science")
        XCTAssertEqual(model.subjects.map(\.name), ["Science"])
        XCTAssertTrue(selectedPath(model).hasSuffix("/Science"))
        XCTAssertEqual(titles(model), ["B 2"])
        model.selection = .folder(model.folders[0].url)
        let science = model.subjects[0].url
        model.rename(science, to: "Physics")
        XCTAssertTrue(selectedPath(model).hasSuffix("/Physics/Waves"), selectedPath(model))
        XCTAssertNil(model.error)

        // A failed rename reports and changes nothing.
        model.selection = .all
        model.rename(b, to: "a/b")
        guard case .invalidName = model.error else { return XCTFail("expected invalidName") }
        model.error = nil
        XCTAssertEqual(Set(titles(model)), ["B", "B 2"])

        // Deleting the selected folder goes back to All Notes.
        model.selection = .folder(model.subjects[0].url)
        model.delete(model.subjects[0].url)
        XCTAssertEqual(model.selection, .all)
        XCTAssertTrue(model.subjects.isEmpty)
        XCTAssertEqual(titles(model), ["B"])
        XCTAssertEqual(model.trash.map(\.title), ["Physics"])

        model.delete(b)
        XCTAssertTrue(model.notes.isEmpty)
        XCTAssertEqual(model.trash.count, 2)
        XCTAssertNil(model.error)
    }

    @MainActor func testFavouriteToggle() throws {
        let note = try makeNote("Lecture")
        try makeNote("Other")
        let model = makeModel()
        let lecture = try XCTUnwrap(model.notes.first { $0.title == "Lecture" })
        XCTAssertFalse(lecture.isFavourite)

        model.toggleFavourite(lecture)
        XCTAssertEqual(model.notes.first { $0.title == "Lecture" }?.isFavourite, true)
        XCTAssertTrue(MetadataStore.load(forNote: note, libraryRoot: root).isFavourite)
        model.selection = .favourites
        XCTAssertEqual(titles(model), ["Lecture"])

        model.toggleFavourite(model.notes[0])
        XCTAssertTrue(model.notes.isEmpty)
        XCTAssertFalse(MetadataStore.load(forNote: note, libraryRoot: root).isFavourite)
    }

    @MainActor func testTrashRestoreAndEmpty() throws {
        let a = try makeNote("A")
        let b = try makeNote("B")
        let model = makeModel()

        model.delete(a)
        XCTAssertEqual(model.trash.map(\.title), ["A"])
        XCTAssertEqual(titles(model), ["B"])
        model.restore(model.trash[0])
        XCTAssertTrue(model.trash.isEmpty)
        XCTAssertEqual(Set(titles(model)), ["A", "B"])
        XCTAssertTrue(FileManager.default.fileExists(atPath: a.path))

        model.delete(a)
        model.delete(b)
        XCTAssertEqual(Set(model.trash.map(\.title)), ["A", "B"])
        let trashedA = try XCTUnwrap(model.trash.first { $0.title == "A" })
        model.deletePermanently(trashedA)
        XCTAssertEqual(model.trash.map(\.title), ["B"])

        model.emptyTrash()
        XCTAssertTrue(model.trash.isEmpty)
        XCTAssertTrue(model.notes.isEmpty)
        XCTAssertFalse(FileManager.default.fileExists(atPath: a.path))
        XCTAssertFalse(FileManager.default.fileExists(atPath: b.path))
        XCTAssertNil(model.error)
    }

    @MainActor func testSortPersisted() throws {
        let older = try makeNote("b")
        let newer = try makeNote("a")
        try MetadataStore.update(forNote: older, libraryRoot: root) { $0.created = Date(timeIntervalSince1970: 100) }
        try MetadataStore.update(forNote: newer, libraryRoot: root) { $0.created = Date(timeIntervalSince1970: 200) }
        let model = makeModel()
        XCTAssertEqual(model.sort, .modified)

        model.sort = .created
        XCTAssertEqual(titles(model), ["a", "b"])
        model.sort = .name
        XCTAssertEqual(titles(model), ["a", "b"])
        try MetadataStore.update(forNote: older, libraryRoot: root) { $0.created = Date(timeIntervalSince1970: 300) }
        model.refresh()
        model.sort = .created
        XCTAssertEqual(titles(model), ["b", "a"])

        model.sort = .name
        XCTAssertEqual(defaults.string(forKey: SettingKey.sort), "name")
        XCTAssertEqual(makeModel().sort, .name)
        XCTAssertEqual(titles(makeModel()), ["a", "b"])

        model.newNoteTemplate = PaperTemplate(pattern: .dotted, color: .cream, size: .letter)
        XCTAssertEqual(makeModel().newNoteTemplate, PaperTemplate(pattern: .dotted, color: .cream, size: .letter))
    }

    @MainActor func testSearchDebounced() async throws {
        try makeNote("Newton laws")
        try makeNote("Roman history")
        let model = makeModel()

        model.searchText = "Rom"
        model.searchText = "Newton"
        // Nothing runs before the pause in typing.
        XCTAssertTrue(model.isSearching)
        XCTAssertTrue(model.searchResults.isEmpty)

        let deadline = Date().addingTimeInterval(30)
        while model.isSearching && Date() < deadline {
            try await Task.sleep(nanoseconds: 50_000_000)
        }
        XCTAssertFalse(model.isSearching, "the search never finished")
        XCTAssertEqual(model.searchResults.map(\.note.title), ["Newton laws"])
        XCTAssertEqual(model.searchResults.first?.titleMatch, true)

        model.searchText = ""
        XCTAssertFalse(model.isSearching)
        XCTAssertTrue(model.searchResults.isEmpty)

        // A query cleared before its pause is over never brings results back.
        model.searchText = "Roman"
        model.searchText = " "
        try await Task.sleep(nanoseconds: 600_000_000)
        XCTAssertTrue(model.searchResults.isEmpty)
        XCTAssertFalse(model.isSearching)
    }

    @MainActor func testImportPDFs() throws {
        let source = TestSupport.temporaryFolder("source")
        defer { try? FileManager.default.removeItem(at: source) }
        let paper = TestSupport.writeNote(TestSupport.pdf(pages: 2), named: "Paper", in: source)
        let fake = source.appendingPathComponent("Fake.pdf")
        try Data("not a pdf".utf8).write(to: fake)
        let physics = try store.createFolder(named: "Physics", in: nil)
        let model = makeModel()
        model.selection = .folder(model.subjects[0].url)

        let imported = model.importPDFs([paper, fake])
        XCTAssertEqual(imported.count, 1)
        let copy = try XCTUnwrap(imported.first)
        XCTAssertEqual(path(copy.deletingLastPathComponent()), path(physics))
        XCTAssertEqual(PDFDocument(url: copy)?.pageCount, 2)
        XCTAssertTrue(FileManager.default.fileExists(atPath: paper.path), "the source is copied, not moved")
        XCTAssertEqual(model.error, .invalidPDF)
        XCTAssertEqual(titles(model), ["Paper"])

        // The same file again gets its own name.
        model.error = nil
        XCTAssertEqual(model.importPDFs([paper]).first?.lastPathComponent, "Paper 2.pdf")
        XCTAssertNil(model.error)
    }

    @MainActor func testNoteFromImages() throws {
        let model = makeModel()
        let wide = image(CGSize(width: 300, height: 200), .red)
        let tall = image(CGSize(width: 200, height: 400), .blue)

        let url = try XCTUnwrap(model.createNote(fromImages: [wide, tall]))
        let document = try XCTUnwrap(PDFDocument(url: url))
        XCTAssertEqual(document.pageCount, 2)
        let first = try XCTUnwrap(document.page(at: 0)).bounds(for: .mediaBox).size
        let second = try XCTUnwrap(document.page(at: 1)).bounds(for: .mediaBox).size
        XCTAssertEqual(first.width / first.height, 1.5, accuracy: 0.01)
        XCTAssertEqual(second.width / second.height, 0.5, accuracy: 0.01)
        XCTAssertEqual(titles(model), [url.deletingPathExtension().lastPathComponent])

        XCTAssertNil(model.createNote(fromImages: []))
        XCTAssertEqual(model.error, .invalidPDF)
        XCTAssertEqual(model.notes.count, 1)
    }

    @MainActor func testWelcomeShownOnFreshInstall() throws {
        let fresh = LibraryLocation(defaults: defaults)
        XCTAssertTrue(fresh.needsChoice)
        let model = LibraryModel(location: fresh, defaults: defaults)
        XCTAssertEqual(model.presentedSheet, .welcome)

        // It comes back on refresh until a choice is made.
        model.presentedSheet = nil
        model.refresh()
        XCTAssertEqual(model.presentedSheet, .welcome)

        XCTAssertTrue(model.chooseLibraryFolder(root))
        XCTAssertNil(model.presentedSheet)
        XCTAssertFalse(fresh.needsChoice)
        XCTAssertEqual(path(fresh.root), path(root))
        model.refresh()
        XCTAssertNil(model.presentedSheet)

        // Once chosen, a relaunch asks nothing; a demo or test root never asks.
        XCTAssertNil(LibraryModel(location: LibraryLocation(defaults: defaults), defaults: defaults).presentedSheet)
        let otherSuite = "zenith-library-tests-\(UUID().uuidString)"
        let otherDefaults = try XCTUnwrap(UserDefaults(suiteName: otherSuite))
        defer { otherDefaults.removePersistentDomain(forName: otherSuite) }
        let demo = LibraryLocation(defaults: otherDefaults)
        demo.useTemporaryRoot(root)
        XCTAssertNil(LibraryModel(location: demo, defaults: otherDefaults).presentedSheet)
    }

    @MainActor func testLocalChoiceShowsWarning() throws {
        let location = LibraryLocation(defaults: defaults)
        let model = LibraryModel(location: location, defaults: defaults)

        XCTAssertTrue(model.chooseLibraryFolder(root))
        XCTAssertFalse(model.isLibraryInsideApp)
        guard case .folder = location.kind else { return XCTFail("expected a folder, got \(location.kind)") }

        model.keepNotesInApp()
        XCTAssertTrue(model.isLibraryInsideApp)
        XCTAssertEqual(location.kind, .local)
        XCTAssertEqual(path(location.root), path(location.localRoot))
        XCTAssertNil(model.presentedSheet)
        XCTAssertFalse(location.needsChoice)

        // A folder that cannot be opened leaves the library where it was and says why.
        XCTAssertFalse(model.chooseLibraryFolder(root.appendingPathComponent("missing", isDirectory: true)))
        XCTAssertTrue(model.isLibraryInsideApp)
        XCTAssertNotNil(model.error)
    }
}
