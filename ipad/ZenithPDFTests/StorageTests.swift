import PDFKit
import UIKit
import XCTest
@testable import ZenithPDF

/// A clock the trash tests move by hand.
private final class TestClock: @unchecked Sendable {
    var date = Date(timeIntervalSince1970: 1_800_000_000)
}

final class StorageTests: XCTestCase {
    private var root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
    private var store = LibraryStore(root: URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true))

    override func setUp() {
        super.setUp()
        root = TestSupport.temporaryFolder(name)
        store = LibraryStore(root: root)
    }

    override func tearDown() {
        try? FileManager.default.removeItem(at: root)
        super.tearDown()
    }

    // MARK: - Helpers

    private func pdfData(pages: Int = 1) -> Data {
        TestSupport.pdf(pages: pages).dataRepresentation() ?? Data()
    }

    private func path(_ url: URL) -> String {
        url.standardizedFileURL.resolvingSymlinksInPath().path
    }

    private func exists(_ url: URL) -> Bool {
        FileManager.default.fileExists(atPath: url.path)
    }

    private func makeNote(_ name: String, in folder: URL? = nil) throws -> URL {
        try store.createNote(named: name, pdfData: pdfData(), template: nil, in: folder)
    }

    private func sidecar(_ note: URL) -> URL {
        SidecarLayout.folder(forNote: note, libraryRoot: root)
    }

    /// Puts a file in the note's sidecar, standing in for recordings and the search index.
    private func addSidecarFile(_ note: URL) throws {
        try FileStore.createFolder(sidecar(note))
        try Data("recordings".utf8).write(to: sidecar(note).appendingPathComponent("recordings.json"))
    }

    private func hasSidecarFile(_ note: URL) -> Bool {
        exists(sidecar(note).appendingPathComponent("recordings.json"))
    }

    private func item(_ title: String, modified: TimeInterval, created: TimeInterval) -> NoteItem {
        NoteItem(url: root.appendingPathComponent(title + ".pdf"), title: title,
                 modified: Date(timeIntervalSince1970: modified), created: Date(timeIntervalSince1970: created),
                 isFavourite: false, folderPath: "")
    }

    // MARK: - Library

    func testCreateNoteFromData() throws {
        let data = pdfData(pages: 2)
        let template = PaperTemplate(pattern: .grid, color: .dark, size: .letter)
        let url = try store.createNote(named: "  Physics.pdf ", pdfData: data, template: template, in: nil)
        XCTAssertEqual(url.lastPathComponent, "Physics.pdf")
        XCTAssertEqual(try Data(contentsOf: url), data)
        let metadata = MetadataStore.load(forNote: url, libraryRoot: root)
        XCTAssertEqual(metadata.template, template)
        XCTAssertNotNil(metadata.created)
        let notes = try store.notes(in: nil, recursive: false)
        XCTAssertEqual(notes.map(\.title), ["Physics"])
        XCTAssertEqual(notes.first.map { path($0.url) }, path(url))
        XCTAssertEqual(notes.first?.folderPath, "")

        let plain = try store.createNote(named: "Plain", pdfData: data, template: nil, in: nil)
        XCTAssertEqual(MetadataStore.load(forNote: plain, libraryRoot: root).template, .standard)
    }

    func testRenameMovesSidecar() throws {
        let note = try makeNote("Old")
        try addSidecarFile(note)
        let renamed = try store.rename(note, to: "New")
        XCTAssertEqual(renamed.lastPathComponent, "New.pdf")
        XCTAssertFalse(exists(note))
        XCTAssertFalse(exists(sidecar(note)))
        XCTAssertTrue(hasSidecarFile(renamed))
        XCTAssertNotNil(MetadataStore.load(forNote: renamed, libraryRoot: root).created)

        let caseOnly = try store.rename(renamed, to: "NEW")
        XCTAssertEqual(caseOnly.lastPathComponent, "NEW.pdf")
        XCTAssertTrue(hasSidecarFile(caseOnly))

        _ = try makeNote("Other")
        XCTAssertThrowsError(try store.rename(caseOnly, to: "Other")) { error in
            XCTAssertEqual(error as? ZenithError, .nameTaken("Other.pdf"))
        }
        XCTAssertTrue(exists(caseOnly))

        let folder = try store.createFolder(named: "Maths", in: nil)
        let inside = try makeNote("Algebra", in: folder)
        try addSidecarFile(inside)
        let renamedFolder = try store.rename(folder, to: "Mathematics")
        XCTAssertEqual(renamedFolder.lastPathComponent, "Mathematics")
        XCTAssertTrue(hasSidecarFile(renamedFolder.appendingPathComponent("Algebra.pdf")))
    }

    func testDuplicateCopiesSidecarWithNewID() throws {
        let note = try makeNote("Lecture")
        try addSidecarFile(note)
        try MetadataStore.update(forNote: note, libraryRoot: root) { $0.noteID = "original-id"; $0.lastPage = 3 }
        let copy = try store.duplicate(note)
        XCTAssertEqual(copy.lastPathComponent, "Lecture 2.pdf")
        XCTAssertEqual(try Data(contentsOf: copy), try Data(contentsOf: note))
        XCTAssertTrue(hasSidecarFile(copy))
        let copied = MetadataStore.load(forNote: copy, libraryRoot: root)
        XCTAssertNotNil(copied.noteID)
        XCTAssertNotEqual(copied.noteID, "original-id")
        XCTAssertEqual(copied.lastPage, 3)
        XCTAssertEqual(MetadataStore.load(forNote: note, libraryRoot: root).noteID, "original-id")

        let folder = try store.createFolder(named: "Term", in: nil)
        let inside = try makeNote("Week 1", in: folder)
        try MetadataStore.update(forNote: inside, libraryRoot: root) { $0.noteID = "inside-id" }
        let folderCopy = try store.duplicate(folder)
        XCTAssertEqual(folderCopy.lastPathComponent, "Term 2")
        let copiedInside = folderCopy.appendingPathComponent("Week 1.pdf")
        XCTAssertTrue(exists(copiedInside))
        let insideID = MetadataStore.load(forNote: copiedInside, libraryRoot: root).noteID
        XCTAssertNotNil(insideID)
        XCTAssertNotEqual(insideID, "inside-id")
    }

    func testMoveMovesSidecar() throws {
        let folder = try store.createFolder(named: "Biology", in: nil)
        let note = try makeNote("Cells")
        try addSidecarFile(note)
        let moved = try store.move(note, to: folder)
        XCTAssertEqual(path(moved), path(folder.appendingPathComponent("Cells.pdf")))
        XCTAssertFalse(exists(note))
        XCTAssertFalse(exists(sidecar(note)))
        XCTAssertTrue(hasSidecarFile(moved))
        XCTAssertEqual(try store.notes(in: folder, recursive: false).first?.folderPath, "Biology")

        // Same name already in the folder: made unique.
        let second = try makeNote("Cells")
        let movedSecond = try store.move(second, to: folder)
        XCTAssertEqual(movedSecond.lastPathComponent, "Cells 2.pdf")

        // Back to the root, and a folder never moves into itself.
        let back = try store.move(moved, to: nil)
        XCTAssertEqual(path(back), path(root.appendingPathComponent("Cells.pdf")))
        XCTAssertTrue(hasSidecarFile(back))
        XCTAssertThrowsError(try store.move(folder, to: folder))
    }

    func testUniqueNames() throws {
        XCTAssertEqual(try makeNote("Untitled").lastPathComponent, "Untitled.pdf")
        XCTAssertEqual(try makeNote("Untitled").lastPathComponent, "Untitled 2.pdf")
        XCTAssertEqual(try makeNote("Untitled").lastPathComponent, "Untitled 3.pdf")
        XCTAssertEqual(try store.createFolder(named: "Chemistry", in: nil).lastPathComponent, "Chemistry")
        XCTAssertEqual(try store.createFolder(named: "Chemistry", in: nil).lastPathComponent, "Chemistry 2")

        // A leftover sidecar keeps its name from being reused.
        try FileStore.createFolder(root.appendingPathComponent(".zenith/Orphan.pdf", isDirectory: true))
        XCTAssertEqual(try makeNote("Orphan").lastPathComponent, "Orphan 2.pdf")

        let desired = root.appendingPathComponent("Free.pdf")
        XCTAssertEqual(FileStore.uniqueURL(desired), desired)
    }

    func testInvalidNamesRejected() throws {
        for name in ["", "   ", "a/b", "a:b", ".hidden", " .pdf"] {
            XCTAssertThrowsError(try FileStore.validatedName(name), name) { error in
                XCTAssertEqual(error as? ZenithError, .invalidName(name))
            }
        }
        XCTAssertEqual(try FileStore.validatedName("  Notes 1 \n"), "Notes 1")
        XCTAssertThrowsError(try store.createNote(named: "a/b", pdfData: pdfData(), template: nil, in: nil))
        XCTAssertThrowsError(try store.createNote(named: ".pdf", pdfData: pdfData(), template: nil, in: nil))
        XCTAssertThrowsError(try store.createFolder(named: ":", in: nil))
        let note = try makeNote("Fine")
        XCTAssertThrowsError(try store.rename(note, to: ".secret"))
        XCTAssertEqual(try store.notes(in: nil, recursive: true).count, 1)
    }

    func testHiddenAndInboxNotListed() throws {
        _ = try makeNote("Visible")
        let inbox = root.appendingPathComponent("Inbox", isDirectory: true)
        try FileStore.createFolder(inbox)
        try pdfData().write(to: inbox.appendingPathComponent("Incoming.pdf"))
        try FileStore.createFolder(root.appendingPathComponent(".hidden", isDirectory: true))
        try pdfData().write(to: root.appendingPathComponent(".hidden/Secret.pdf"))
        try pdfData().write(to: root.appendingPathComponent(".dot.pdf"))
        try Data("x".utf8).write(to: root.appendingPathComponent("readme.txt"))
        try Data().write(to: root.appendingPathComponent(".Cloud.pdf.icloud"))
        let sub = try store.createFolder(named: "Sub", in: nil)
        try FileStore.createFolder(sub.appendingPathComponent("Inbox", isDirectory: true))

        XCTAssertEqual(try store.folders(in: nil).map(\.name), ["Sub"])
        XCTAssertEqual(try store.folders(in: sub).map(\.name), ["Inbox"])
        XCTAssertEqual(Set(try store.notes(in: nil, recursive: false).map(\.title)), ["Visible", "Cloud"])
        XCTAssertEqual(Set(try store.notes(in: nil, recursive: true).map(\.title)), ["Visible", "Cloud"])
        let cloud = try store.notes(in: nil, recursive: false).first { $0.title == "Cloud" }
        XCTAssertEqual(cloud?.url.lastPathComponent, "Cloud.pdf")
    }

    func testRecursiveListing() throws {
        _ = try makeNote("A")
        let s = try store.createFolder(named: "S", in: nil)
        _ = try makeNote("B", in: s)
        let t = try store.createFolder(named: "T", in: s)
        _ = try makeNote("C", in: t)
        try addSidecarFile(root.appendingPathComponent("A.pdf"))

        XCTAssertEqual(try store.notes(in: nil, recursive: false).map(\.title), ["A"])
        let all = try store.notes(in: nil, recursive: true)
        XCTAssertEqual(Dictionary(uniqueKeysWithValues: all.map { ($0.title, $0.folderPath) }),
                       ["A": "", "B": "S", "C": "S/T"])
        XCTAssertEqual(try store.notes(in: s, recursive: true).count, 2)
        XCTAssertEqual(try store.folders(in: nil).first?.noteCount, 1)
        XCTAssertEqual(path(store.url(forRelativePath: "S/T")), path(t))
        XCTAssertEqual(store.relativePath(of: t), "S/T")
        XCTAssertEqual(store.relativePath(of: root), "")
    }

    func testFavouritePersists() throws {
        let note = try makeNote("Star")
        _ = try makeNote("Plain")
        try store.setFavourite(true, for: note)
        XCTAssertTrue(MetadataStore.load(forNote: note, libraryRoot: root).isFavourite)
        let fresh = LibraryStore(root: root)
        XCTAssertEqual(try fresh.favourites().map(\.title), ["Star"])
        XCTAssertEqual(try fresh.notes(in: nil, recursive: false).first { $0.title == "Star" }?.isFavourite, true)
        try fresh.setFavourite(false, for: note)
        XCTAssertTrue(try store.favourites().isEmpty)
    }

    func testMetadataUpdateKeepsOtherFields() throws {
        let note = try makeNote("Kept")
        let created = MetadataStore.load(forNote: note, libraryRoot: root).created
        let template = PaperTemplate(pattern: .dotted, color: .cream, size: .a4)
        try MetadataStore.update(forNote: note, libraryRoot: root) { metadata in
            metadata.template = template
            metadata.lastPage = 7
            metadata.noteID = "kept-id"
        }
        try store.setFavourite(true, for: note)
        let metadata = MetadataStore.load(forNote: note, libraryRoot: root)
        XCTAssertEqual(metadata, NoteMetadata(isFavourite: true, template: template, created: created, lastPage: 7, noteID: "kept-id"))

        // A bad meta.json reads as defaults and the next update starts from them.
        try Data("not json".utf8).write(to: SidecarLayout.metadata(forNote: note, libraryRoot: root))
        XCTAssertEqual(MetadataStore.load(forNote: note, libraryRoot: root), NoteMetadata())
        try MetadataStore.update(forNote: note, libraryRoot: root) { $0.lastPage = 1 }
        XCTAssertEqual(MetadataStore.load(forNote: note, libraryRoot: root).lastPage, 1)
    }

    func testOrphanedSidecarAdopted() throws {
        let note = try makeNote("Before")
        try addSidecarFile(note)
        try MetadataStore.update(forNote: note, libraryRoot: root) { $0.noteID = "id-1" }
        // Renamed on the PC: the PDF moves, its sidecar stays behind.
        let renamed = root.appendingPathComponent("After.pdf")
        try FileManager.default.moveItem(at: note, to: renamed)
        let adopted = try MetadataStore.adoptOrphanedSidecar(forNote: renamed, noteID: "id-1", libraryRoot: root)
        XCTAssertEqual(adopted.map(path), path(sidecar(renamed)))
        XCTAssertTrue(hasSidecarFile(renamed))
        XCTAssertFalse(exists(sidecar(note)))

        // Moved into another folder on the PC: found in the old folder's .zenith.
        let folder = try store.createFolder(named: "Elsewhere", in: nil)
        let moved = folder.appendingPathComponent("Moved.pdf")
        try FileManager.default.moveItem(at: renamed, to: moved)
        XCTAssertNotNil(try MetadataStore.adoptOrphanedSidecar(forNote: moved, noteID: "id-1", libraryRoot: root))
        XCTAssertTrue(hasSidecarFile(moved))
        XCTAssertEqual(MetadataStore.load(forNote: moved, libraryRoot: root).noteID, "id-1")

        // Nothing with that id: nothing adopted, and a note that has its sidecar keeps it.
        let lonely = try makeNote("Lonely")
        try FileStore.remove(sidecar(lonely))
        XCTAssertNil(try MetadataStore.adoptOrphanedSidecar(forNote: lonely, noteID: "id-unknown", libraryRoot: root))
        XCTAssertEqual(try MetadataStore.adoptOrphanedSidecar(forNote: moved, noteID: "id-1", libraryRoot: root).map(path),
                       path(sidecar(moved)))
    }

    func testCopyDoesNotStealSidecar() throws {
        let original = try makeNote("Original")
        try addSidecarFile(original)
        try MetadataStore.update(forNote: original, libraryRoot: root) { $0.noteID = "shared-id" }
        // A copy made on the PC carries the same id while the original is still there.
        let copy = root.appendingPathComponent("Original copy.pdf")
        try FileManager.default.copyItem(at: original, to: copy)
        XCTAssertNil(try MetadataStore.adoptOrphanedSidecar(forNote: copy, noteID: "shared-id", libraryRoot: root))
        XCTAssertTrue(hasSidecarFile(original))
        XCTAssertFalse(exists(sidecar(copy)))
    }

    func testSortOrders() {
        let notes = [item("beta", modified: 300, created: 100),
                     item("Alpha", modified: 100, created: 300),
                     item("gamma 10", modified: 200, created: 200),
                     item("gamma 9", modified: 200, created: 50)]
        XCTAssertEqual(LibraryStore.sorted(notes, by: .modified).map(\.title), ["beta", "gamma 9", "gamma 10", "Alpha"])
        XCTAssertEqual(LibraryStore.sorted(notes, by: .created).map(\.title), ["Alpha", "gamma 10", "beta", "gamma 9"])
        XCTAssertEqual(LibraryStore.sorted(notes, by: .name).map(\.title), ["Alpha", "beta", "gamma 9", "gamma 10"])
    }

    // MARK: - Trash

    func testTrashRestoreSidecar() throws {
        let folder = try store.createFolder(named: "History", in: nil)
        let note = try makeNote("Rome", in: folder)
        try addSidecarFile(note)
        let trash = TrashStore(library: store)
        let item = try trash.trash(note)
        XCTAssertEqual(item.title, "Rome")
        XCTAssertEqual(item.originalPath, "History/Rome.pdf")
        XCTAssertFalse(item.isFolder)
        XCTAssertFalse(exists(note))
        XCTAssertFalse(exists(sidecar(note)))
        XCTAssertTrue(try store.notes(in: nil, recursive: true).isEmpty)
        XCTAssertEqual(try trash.items(), [item])

        let restored = try trash.restore(item)
        XCTAssertEqual(path(restored), path(note))
        XCTAssertTrue(hasSidecarFile(restored))
        XCTAssertTrue(try trash.items().isEmpty)

        // Restored again after its folder was deleted and the name was taken.
        let again = try trash.trash(restored)
        try FileStore.remove(folder)
        let restoredFolder = try store.createFolder(named: "History", in: nil)
        _ = try makeNote("Rome", in: restoredFolder)
        let second = try trash.restore(again)
        XCTAssertEqual(path(second), path(restoredFolder.appendingPathComponent("Rome 2.pdf")))
        XCTAssertTrue(hasSidecarFile(second))
    }

    func testTrashFolder() throws {
        let folder = try store.createFolder(named: "Old Term", in: nil)
        let inside = try makeNote("Notes", in: folder)
        try addSidecarFile(inside)
        let trash = TrashStore(library: store)
        let item = try trash.trash(folder)
        XCTAssertTrue(item.isFolder)
        XCTAssertEqual(item.title, "Old Term")
        XCTAssertTrue(try store.folders(in: nil).isEmpty)
        XCTAssertTrue(try store.notes(in: nil, recursive: true).isEmpty)

        let restored = try trash.restore(item)
        XCTAssertEqual(path(restored), path(folder))
        XCTAssertTrue(hasSidecarFile(inside))
    }

    func testPurgeAfter30Days() throws {
        let clock = TestClock()
        let trash = TrashStore(library: store, now: { clock.date })
        let start = clock.date
        _ = try trash.trash(makeNote("Ancient"))
        clock.date = start.addingTimeInterval(25 * 86_400)
        let recent = try trash.trash(makeNote("Recent"))
        clock.date = start.addingTimeInterval(31 * 86_400)
        XCTAssertEqual(try trash.purge(), 1)
        XCTAssertEqual(try trash.items().map(\.title), [recent.title])
        XCTAssertEqual(try trash.purge(), 0)
        clock.date = start.addingTimeInterval(56 * 86_400)
        XCTAssertEqual(try trash.purge(olderThanDays: 30), 1)
        XCTAssertTrue(try trash.items().isEmpty)
    }

    func testEmptyTrash() throws {
        let trash = TrashStore(library: store)
        let first = try trash.trash(makeNote("One"))
        _ = try trash.trash(store.createFolder(named: "Two", in: nil))
        XCTAssertEqual(try trash.items().count, 2)
        try trash.deletePermanently(first)
        XCTAssertEqual(try trash.items().map(\.title), ["Two"])
        try trash.empty()
        XCTAssertTrue(try trash.items().isEmpty)
        XCTAssertFalse(exists(root.appendingPathComponent(SidecarLayout.trashName)))
        XCTAssertNoThrow(try trash.empty())
    }

    // MARK: - Safe replace

    func testReplaceIsAtomicOnFailure() throws {
        let folder = root.appendingPathComponent("Locked", isDirectory: true)
        try FileStore.createFolder(folder)
        let url = folder.appendingPathComponent("Note.pdf")
        let original = Data("original".utf8)
        try original.write(to: url)
        // Neither the swap nor the atomic fallback can create a file in a read-only folder.
        try FileManager.default.setAttributes([.posixPermissions: 0o555], ofItemAtPath: folder.path)
        defer { try? FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: folder.path) }
        XCTAssertThrowsError(try FileStore.replace(url, with: Data("new".utf8)))
        XCTAssertEqual(try Data(contentsOf: url), original)
    }

    func testReplaceKeepsPrevious() throws {
        let url = root.appendingPathComponent("Note.pdf")
        let previous = sidecar(url).appendingPathComponent("previous.pdf")
        // A new file: created, nothing to keep.
        let date = try FileStore.replace(url, with: Data("first".utf8), keepPrevious: previous)
        XCTAssertEqual(try Data(contentsOf: url), Data("first".utf8))
        XCTAssertFalse(exists(previous))
        XCTAssertEqual(FileStore.modificationDate(url), date)

        try FileStore.replace(url, with: Data("second".utf8), keepPrevious: previous)
        XCTAssertEqual(try Data(contentsOf: url), Data("second".utf8))
        XCTAssertEqual(try Data(contentsOf: previous), Data("first".utf8))
        try FileStore.replace(url, with: Data("third".utf8), keepPrevious: previous)
        XCTAssertEqual(try Data(contentsOf: previous), Data("second".utf8))
        try FileStore.replace(url, with: Data("fourth".utf8))
        XCTAssertEqual(try Data(contentsOf: previous), Data("second".utf8))
        XCTAssertEqual(try FileStore.read(url), Data("fourth".utf8))
        XCTAssertEqual(FileStore.sha256(Data("abc".utf8)),
                       "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
    }

    func testReplaceFallsBackWhenReplacementDirectoryFails() throws {
        let saved = FileStore.replacementDirectory
        defer { FileStore.replacementDirectory = saved }
        var asked = 0
        FileStore.replacementDirectory = { _ in
            asked += 1
            throw ZenithError.accessDenied(URL(fileURLWithPath: "/"))
        }
        let url = root.appendingPathComponent("Note.pdf")
        try Data("before".utf8).write(to: url)
        try FileStore.replace(url, with: Data("after".utf8))
        XCTAssertEqual(asked, 1)
        XCTAssertEqual(try Data(contentsOf: url), Data("after".utf8))
    }

    // MARK: - Location

    @MainActor func testLocationFallsBackToLocal() throws {
        let suite = "zenith-tests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        defaults.set(Data([1, 2, 3]), forKey: SettingKey.libraryBookmark)
        defaults.set(true, forKey: SettingKey.libraryChoiceMade)
        let location = LibraryLocation(defaults: defaults)
        location.resolve()
        XCTAssertEqual(location.root, location.localRoot)
        XCTAssertEqual(location.kind, .local)
        XCTAssertNotNil(location.problem)
        XCTAssertFalse(location.needsChoice)

        // A folder that was chosen resolves again on the next launch.
        let chosen = LibraryLocation(defaults: defaults)
        try chosen.choose(folder: root)
        XCTAssertNotNil(defaults.data(forKey: SettingKey.libraryBookmark))
        let relaunched = LibraryLocation(defaults: defaults)
        relaunched.resolve()
        XCTAssertNil(relaunched.problem)
        XCTAssertEqual(path(relaunched.root), path(root))
        guard case .folder = relaunched.kind else { return XCTFail("expected a folder, got \(relaunched.kind)") }

        XCTAssertThrowsError(try chosen.choose(folder: root.appendingPathComponent("missing", isDirectory: true)))
    }

    @MainActor func testNeedsChoiceOnFreshInstall() throws {
        let suite = "zenith-tests-\(UUID().uuidString)"
        let defaults = try XCTUnwrap(UserDefaults(suiteName: suite))
        defer { defaults.removePersistentDomain(forName: suite) }
        let location = LibraryLocation(defaults: defaults)
        XCTAssertTrue(location.needsChoice)
        location.resolve()
        XCTAssertTrue(location.needsChoice)
        XCTAssertEqual(location.kind, .local)
        XCTAssertNil(location.problem)

        let demo = LibraryLocation(defaults: defaults)
        demo.useTemporaryRoot(root)
        XCTAssertFalse(demo.needsChoice)
        XCTAssertEqual(demo.root, root)
        XCTAssertFalse(defaults.bool(forKey: SettingKey.libraryChoiceMade))

        location.useLocal()
        XCTAssertFalse(location.needsChoice)
        XCTAssertTrue(defaults.bool(forKey: SettingKey.libraryChoiceMade))
        XCTAssertFalse(LibraryLocation(defaults: defaults).needsChoice)
    }

    // MARK: - Thumbnails

    func testThumbnailCachedByModificationDate() async throws {
        let cacheFolder = root.appendingPathComponent("Cache", isDirectory: true)
        let cache = ThumbnailCache(folder: cacheFolder)
        let note = try makeNote("Picture")
        let first = Date(timeIntervalSince1970: 1_700_000_000)

        let image = await cache.thumbnail(for: note, modified: first, width: 120)
        XCTAssertEqual(image.map { $0.size.width.rounded() }, 120)
        let ratio = PaperSize.a4.points.height / PaperSize.a4.points.width
        XCTAssertEqual(Double(image.map { $0.size.height / $0.size.width } ?? 0), Double(ratio), accuracy: 0.02)
        let cachedFiles = try FileManager.default.contentsOfDirectory(atPath: cacheFolder.path)
        XCTAssertEqual(cachedFiles.count, 1)
        XCTAssertTrue(cachedFiles.first?.hasSuffix("-1700000000000.png") ?? false)

        // Served from the cache while the date is the same, even with the note gone.
        try FileManager.default.removeItem(at: note)
        let again = await cache.thumbnail(for: note, modified: first, width: 120)
        XCTAssertNotNil(again)
        // A new date is a new thumbnail, never the old one.
        let later = await cache.thumbnail(for: note, modified: first.addingTimeInterval(5), width: 120)
        XCTAssertNil(later)

        await cache.invalidate(note)
        XCTAssertTrue(try FileManager.default.contentsOfDirectory(atPath: cacheFolder.path).isEmpty)
        let afterInvalidate = await cache.thumbnail(for: note, modified: first, width: 120)
        XCTAssertNil(afterInvalidate)
    }
}
