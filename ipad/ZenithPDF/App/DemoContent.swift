import AVFoundation
import Foundation
import PDFKit
import PencilKit
import UIKit

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
        var editor = LaunchOptions.baseEditorLaunch()
        if scene == .empty {
            let root = documents.appendingPathComponent("DemoEmpty", isDirectory: true)
            if FileStore.exists(root) { try FileManager.default.removeItem(at: root) }
            try FileStore.createFolder(root)
            return DemoState(root: root, openNote: nil, editor: editor, selection: .all, searchText: "", sheet: nil)
        }

        let root = documents.appendingPathComponent("Demo", isDirectory: true)
        let versionFile = root.appendingPathComponent("version", isDirectory: false)
        let built = (try? String(contentsOf: versionFile, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
        if built != String(version) {
            if FileStore.exists(root) { try FileManager.default.removeItem(at: root) }
            try FileStore.createFolder(root)
            try build(in: root)
            // Written last, so a build that stopped halfway is built again on the next launch.
            try Data(String(version).utf8).write(to: versionFile, options: .atomic)
        }

        let lecture = root.appendingPathComponent(Name.lecture, isDirectory: false)
        let desktop = root.appendingPathComponent(Name.desktop, isDirectory: false)
        var openNote: URL? = nil
        var selection = SidebarSelection.all
        var searchText = ""
        var sheet: LibrarySheet? = nil
        switch scene {
        case .library, .empty:
            break
        case .note:
            openNote = lecture
        case .darkPaper:
            openNote = root.appendingPathComponent(Name.night, isDirectory: false)
        case .zoomed:
            openNote = lecture
            editor.zoom = 2.5
            editor.focus = CGPoint(x: 50, y: 50)
        case .pages:
            openNote = lecture
            editor.showsPages = true
        case .templates:
            sheet = .newNote
        case .search:
            searchText = "Newton"
        case .noteSearch:
            openNote = lecture
            editor.showsSearch = true
            editor.searchQuery = "Newton"
        case .trash:
            selection = .recentlyDeleted
        case .audio:
            openNote = lecture
            editor.replayAt = recordingSeconds * 0.4
        case .settings:
            sheet = .settings
        case .desktopNote:
            openNote = FileStore.exists(desktop) ? desktop : lecture
        }
        return DemoState(root: root, openNote: openNote, editor: editor, selection: selection,
                         searchText: searchText, sheet: sheet)
    }

    // MARK: - Names and constants

    /// Paths relative to the demo root.
    private enum Name {
        static let lecture = "Physics/Newton's Laws.pdf"
        static let night = "Night Sketches.pdf"
        static let desktop = "Desktop Ink.pdf"
    }

    private static let recordingSeconds: TimeInterval = 20
    private static let black = UIColor.black
    private static let blue = UIColor(red: 0.14, green: 0.34, blue: 0.84, alpha: 1)
    private static let yellow = UIColor(red: 1, green: 0.84, blue: 0.2, alpha: 1)
    private static let white = UIColor.white
    private static let lined = (first: CGFloat(25 * 72 / 25.4), step: CGFloat(8 * 72 / 25.4))
    private static let college = (first: CGFloat(25 * 72 / 25.4), step: CGFloat(20.25))

    // MARK: - Building

    private static func build(in root: URL) throws {
        let store = LibraryStore(root: root)
        let physics = try store.createFolder(named: "Physics", in: nil)
        let history = try store.createFolder(named: "History", in: nil)
        let recipes = try store.createFolder(named: "Recipes", in: nil)
        let past = Date().addingTimeInterval(-7200)

        // Recently Deleted: written first, then moved to the trash.
        var draft = PageSpec(template: PaperTemplate(pattern: .blank, color: .white, size: .a4))
        draft.write("first draft, ignore", baseline: CGPoint(x: 60, y: 110), size: 22, color: black, at: past)
        let draftURL = try makeNote("Old Draft", in: nil, store: store, pages: [draft])
        var scratch = PageSpec(template: PaperTemplate(pattern: .grid, color: .dark, size: .letter))
        scratch.write("scratch", baseline: CGPoint(x: 60, y: 110), size: 24, color: white, at: past)
        let scratchURL = try makeNote("Scratch Page", in: nil, store: store, pages: [scratch])
        let trash = TrashStore(library: store)
        _ = try trash.trash(draftURL)
        _ = try trash.trash(scratchURL)

        try makeTimeline(in: history, store: store, date: past)
        let revolution = try makeRevolution(in: history, store: store, date: past)
        try store.setFavourite(true, for: revolution)
        try makeRecipes(in: recipes, store: store, date: past)
        try makeMeeting(store: store, date: past)
        try makeProblemSet(in: physics, store: store, date: past)
        try makeNight(store: store, date: past)

        if let fixture = Bundle.main.url(forResource: "desktop-ink", withExtension: "pdf")
            ?? Bundle.main.url(forResource: "desktop-ink", withExtension: "pdf", subdirectory: "Demo") {
            try FileStore.copy(fixture, to: root.appendingPathComponent(Name.desktop, isDirectory: false))
        } else {
            log.info("No desktop fixture in the bundle: the desktopNote scene shows the lecture")
        }

        // Last, so it is the most recently changed note and leads the grid.
        try makeLecture(in: physics, store: store, past: past)
    }

    private static func makeLecture(in folder: URL, store: LibraryStore, past: Date) throws {
        let start = Date().addingTimeInterval(-3600)
        let rule = { (k: Int) in lined.first + CGFloat(k) * lined.step }

        var first = PageSpec(template: .standard)
        first.type("Lecture 4: Newton's Laws of Motion", at: CGPoint(x: 72, y: 30),
                   font: .systemFont(ofSize: 20, weight: .semibold))
        let law = "Second law: the net force on a body equals its mass times its acceleration."
        let lawFont = UIFont.systemFont(ofSize: 12)
        first.type(law, at: CGPoint(x: 80, y: rule(1) - lawFont.ascender), font: lawFont)
        let lawWidth = (law as NSString).size(withAttributes: [.font: lawFont]).width
        first.highlight(from: CGPoint(x: 78, y: rule(1) - 4), to: CGPoint(x: 82 + lawWidth, y: rule(1) - 4),
                        color: yellow, at: past)
        // Written during the recording, a few seconds per line, so a replay at 40% shows the later lines faint.
        first.write("Newton's first law: inertia", baseline: CGPoint(x: 80, y: rule(3)), size: 20, color: black,
                    at: start.addingTimeInterval(1), seconds: 3)
        first.write("a body keeps its motion unless a force acts", baseline: CGPoint(x: 80, y: rule(4)), size: 20,
                    color: black, at: start.addingTimeInterval(4.5), seconds: 3)
        first.write("F = m a", baseline: CGPoint(x: 120, y: rule(6) + 4), size: 30, color: blue,
                    at: start.addingTimeInterval(8), seconds: 1.5)
        let formulaWidth = DemoInk.width("F = m a", size: 30)
        first.shape(.rectangle(CGRect(x: 108, y: rule(5) - 4, width: formulaWidth + 26, height: 44)), color: blue,
                    at: start.addingTimeInterval(9.8))
        first.write("Newton 1687, Principia", baseline: CGPoint(x: 80, y: rule(8)), size: 20, color: blue,
                    at: start.addingTimeInterval(11.5), seconds: 3)
        first.shape(.line(CGPoint(x: 80, y: rule(8) + 5), CGPoint(x: 80 + DemoInk.width("Newton 1687, Principia", size: 20),
                                                                     y: rule(8) + 5)),
                    color: blue, at: start.addingTimeInterval(14.6))
        first.write("third law: action and reaction", baseline: CGPoint(x: 80, y: rule(9)), size: 20, color: black,
                    at: start.addingTimeInterval(15.5), seconds: 3)
        var examStyle = TextBoxStyle()
        examStyle.size = 13
        examStyle.bold = true
        examStyle.color = RGB(blue)
        first.textBoxes.append(TextBoxSpec(text: "Exam: chapters 4 and 5", topLeft: CGPoint(x: 360, y: rule(11)),
                                           width: 180, style: examStyle))
        first.pictures.append(PictureSpec(symbol: "atom", origin: CGPoint(x: 380, y: rule(13)), width: 150))

        var second = PageSpec(template: PaperTemplate(pattern: .grid, color: .white, size: .a4))
        second.write("Free body diagram", baseline: CGPoint(x: 60, y: 80), size: 24, color: black, at: past)
        second.shape(.rectangle(CGRect(x: 230, y: 300, width: 120, height: 80)), color: black, at: past)
        second.shape(.line(CGPoint(x: 290, y: 300), CGPoint(x: 290, y: 200)), color: blue, at: past)
        second.shape(.line(CGPoint(x: 290, y: 380), CGPoint(x: 290, y: 480)), color: blue, at: past)
        second.write("N", baseline: CGPoint(x: 300, y: 215), size: 22, color: blue, at: past)
        second.write("mg", baseline: CGPoint(x: 300, y: 480), size: 22, color: blue, at: past)

        var third = PageSpec(template: PaperTemplate(pattern: .dotted, color: .white, size: .a4))
        third.write("Orbits and Newton's gravity", baseline: CGPoint(x: 60, y: 80), size: 24, color: black, at: past)
        third.shape(.ellipse(CGRect(x: 140, y: 220, width: 320, height: 180)), color: blue, at: past)
        third.shape(.ellipse(CGRect(x: 280, y: 290, width: 40, height: 40)), color: black, at: past)

        var fourth = PageSpec(template: PaperTemplate(pattern: .collegeRuled, color: .white, size: .a4))
        let collegeRule = { (k: Int) in college.first + CGFloat(k) * college.step }
        fourth.write("Third law", baseline: CGPoint(x: 80, y: collegeRule(1)), size: 20, color: black, at: past)
        fourth.write("every action has an equal and opposite reaction", baseline: CGPoint(x: 80, y: collegeRule(2)),
                     size: 18, color: black, at: past)

        // Twenty pages, so the UI test can scroll a long note.
        let rest = (0..<16).map { _ in PageSpec(template: .standard) }
        let url = try makeNote("Newton's Laws", in: folder, store: store, template: .standard,
                               pages: [first, second, third, fourth] + rest)

        let id = UUID()
        let recording = Recording(id: id, fileName: id.uuidString + ".m4a", startedAt: start,
                                  duration: recordingSeconds, title: "Recording 1")
        try writeTone(to: RecordingStore.fileURL(for: recording, note: url, libraryRoot: store.root),
                      seconds: recordingSeconds)
        try RecordingStore.save([recording], note: url, libraryRoot: store.root)
    }

    private static func makeNight(store: LibraryStore, date: Date) throws {
        var page = PageSpec(template: PaperTemplate(pattern: .dotted, color: .dark, size: .a4))
        page.write("Night sky, November", baseline: CGPoint(x: 60, y: 90), size: 28, color: white, at: date)
        page.write("Orion rises in the east", baseline: CGPoint(x: 60, y: 140), size: 20, color: yellow, at: date)
        for center in [CGPoint(x: 200, y: 300), CGPoint(x: 330, y: 260), CGPoint(x: 260, y: 420),
                       CGPoint(x: 400, y: 380)] {
            page.shape(.polygon(star(center: center, radius: 18)), color: yellow, at: date)
        }
        page.shape(.ellipse(CGRect(x: 420, y: 180, width: 70, height: 70)), color: white, at: date)
        page.write("the moon, almost full", baseline: CGPoint(x: 340, y: 290), size: 18, color: white, at: date)
        try makeNote("Night Sketches", in: nil, store: store, pages: [page])
    }

    private static func makeProblemSet(in folder: URL, store: LibraryStore, date: Date) throws {
        var page = PageSpec(template: PaperTemplate(pattern: .grid, color: .cream, size: .a4))
        page.write("1. A 2 kg cart is pushed with 6 N", baseline: CGPoint(x: 50, y: 90), size: 20, color: black, at: date)
        page.write("a = F / m = 3 m/s2", baseline: CGPoint(x: 70, y: 140), size: 22, color: blue, at: date)
        page.shape(.rectangle(CGRect(x: 60, y: 112, width: DemoInk.width("a = F / m = 3 m/s2", size: 22) + 22,
                                     height: 40)), color: blue, at: date)
        page.write("2. Newton's third law on a rocket", baseline: CGPoint(x: 50, y: 220), size: 20, color: black, at: date)
        try makeNote("Problem Set 2", in: folder, store: store, pages: [page])
    }

    private static func makeRevolution(in folder: URL, store: LibraryStore, date: Date) throws -> URL {
        var page = PageSpec(template: PaperTemplate(pattern: .collegeRuled, color: .cream, size: .letter))
        let rule = { (k: Int) in college.first + CGFloat(k) * college.step }
        page.type("The French Revolution", at: CGPoint(x: 72, y: 30), font: .systemFont(ofSize: 20, weight: .semibold))
        page.write("1789 Estates General", baseline: CGPoint(x: 80, y: rule(2)), size: 18, color: black, at: date)
        page.write("Tennis Court Oath, 20 June", baseline: CGPoint(x: 80, y: rule(3)), size: 18, color: black, at: date)
        page.highlight(from: CGPoint(x: 78, y: rule(3) - 6),
                       to: CGPoint(x: 82 + DemoInk.width("Tennis Court Oath, 20 June", size: 18), y: rule(3) - 6),
                       color: yellow, at: date)
        page.write("14 July: the Bastille", baseline: CGPoint(x: 80, y: rule(4)), size: 18, color: blue, at: date)
        return try makeNote("The French Revolution", in: folder, store: store, pages: [page])
    }

    private static func makeTimeline(in folder: URL, store: LibraryStore, date: Date) throws {
        var page = PageSpec(template: PaperTemplate(pattern: .blank, color: .white, size: .letter))
        page.write("Timeline", baseline: CGPoint(x: 60, y: 90), size: 28, color: black, at: date)
        page.shape(.line(CGPoint(x: 70, y: 260), CGPoint(x: 540, y: 260)), color: black, at: date)
        for (index, year) in ["1789", "1793", "1799"].enumerated() {
            let x = 100 + CGFloat(index) * 170
            page.shape(.line(CGPoint(x: x, y: 245), CGPoint(x: x, y: 275)), color: black, at: date)
            page.write(year, baseline: CGPoint(x: x - 20, y: 310), size: 20, color: blue, at: date)
        }
        try makeNote("Timeline", in: folder, store: store, pages: [page])
    }

    private static func makeRecipes(in folder: URL, store: LibraryStore, date: Date) throws {
        var bread = PageSpec(template: PaperTemplate(pattern: .dotted, color: .cream, size: .a4))
        bread.write("Sourdough", baseline: CGPoint(x: 60, y: 90), size: 28, color: black, at: date)
        for (index, line) in ["Levain 100 g", "Flour 500 g", "Water 350 g", "Salt 10 g"].enumerated() {
            bread.write(line, baseline: CGPoint(x: 70, y: 150 + CGFloat(index) * 36), size: 20, color: black, at: date)
        }
        bread.pictures.append(PictureSpec(symbol: "fork.knife", origin: CGPoint(x: 380, y: 130), width: 120))
        try makeNote("Sourdough", in: folder, store: store, pages: [bread])

        var pasta = PageSpec(template: PaperTemplate(pattern: .lined, color: .cream, size: .letter))
        let rule = { (k: Int) in lined.first + CGFloat(k) * lined.step }
        pasta.write("Weeknight pasta", baseline: CGPoint(x: 80, y: rule(1)), size: 22, color: black, at: date)
        pasta.write("garlic, chilli, lemon", baseline: CGPoint(x: 80, y: rule(2)), size: 18, color: blue, at: date)
        try makeNote("Weeknight Pasta", in: folder, store: store, pages: [pasta])
    }

    private static func makeMeeting(store: LibraryStore, date: Date) throws {
        var page = PageSpec(template: PaperTemplate(pattern: .lined, color: .white, size: .letter))
        let rule = { (k: Int) in lined.first + CGFloat(k) * lined.step }
        page.type("Study group", at: CGPoint(x: 72, y: 30), font: .systemFont(ofSize: 20, weight: .semibold))
        page.write("review chapter 4 on Thursday", baseline: CGPoint(x: 80, y: rule(1)), size: 18, color: black, at: date)
        page.write("bring the problem set", baseline: CGPoint(x: 80, y: rule(2)), size: 18, color: black, at: date)
        try makeNote("Meeting Notes", in: nil, store: store, pages: [page])
    }

    // MARK: - Pages and notes

    private struct TypedText { let text: String; let origin: CGPoint; let font: UIFont }
    private struct TextBoxSpec { let text: String; let topLeft: CGPoint; let width: CGFloat; let style: TextBoxStyle }
    private struct PictureSpec { let symbol: String; let origin: CGPoint; let width: CGFloat }

    /// One page of a demo note, display space.
    private struct PageSpec {
        var template: PaperTemplate
        var typed: [TypedText] = []
        var strokes: [PKStroke] = []
        var words: [InkWord] = []
        var textBoxes: [TextBoxSpec] = []
        var pictures: [PictureSpec] = []

        init(template: PaperTemplate) {
            self.template = template
        }

        /// Text drawn into the page itself, so it is the page's typed text.
        mutating func type(_ text: String, at origin: CGPoint, font: UIFont) {
            typed.append(TypedText(text: text, origin: origin, font: font))
        }

        /// Handwriting sitting on `baseline`; its words go into the search index.
        mutating func write(_ text: String, baseline: CGPoint, size: CGFloat, color: UIColor, at date: Date,
                            seconds: TimeInterval = 2) {
            let origin = CGPoint(x: baseline.x, y: baseline.y - DemoInk.ascent(size: size))
            strokes += DemoInk.strokes(text, at: origin, size: size, ink: PKInk(.pen, color: color), startingAt: date,
                                       secondsPerStroke: seconds / Double(max(text.count, 1)))
            words += DemoInk.words(text, at: origin, size: size)
        }

        /// A shape as shape recognition writes it.
        mutating func shape(_ shape: RecognizedShape, color: UIColor, at date: Date) {
            let original = DemoInk.underline(from: .zero, to: CGPoint(x: 10, y: 0), ink: PKInk(.pen, color: color),
                                             date: date)
            strokes += ShapeStrokeBuilder.strokes(for: shape, replacing: original)
        }

        mutating func highlight(from start: CGPoint, to end: CGPoint, color: UIColor, at date: Date) {
            let steps = max(Int(PolylineMath.distance(start, end) / 3), 1)
            let points = (0...steps).map { step -> PKStrokePoint in
                let t = CGFloat(step) / CGFloat(steps)
                return PKStrokePoint(location: CGPoint(x: start.x + (end.x - start.x) * t, y: start.y + (end.y - start.y) * t),
                                     timeOffset: TimeInterval(step) * 0.01, size: CGSize(width: 14, height: 14),
                                     opacity: 1, force: 1, azimuth: 0, altitude: .pi / 2)
            }
            // See-through, as a highlighter is: the canvas sits over the page's typed text.
            strokes.append(PKStroke(ink: PKInk(.marker, color: color.withAlphaComponent(0.45)),
                                    path: PKStrokePath(controlPoints: points, creationDate: date),
                                    transform: .identity, mask: nil))
        }
    }

    /// Renders the pages (template, typed text), adds text boxes and pictures, writes the ink through NoteWriter,
    /// creates the note in the library and writes its search index from the known words.
    @discardableResult
    private static func makeNote(_ name: String, in folder: URL?, store: LibraryStore, template: PaperTemplate? = nil,
                                 pages: [PageSpec]) throws -> URL {
        let data = try render(pages)
        let url = try store.createNote(named: name, pdfData: data, template: template ?? pages.first?.template,
                                       in: folder)
        try writeIndex(note: url, root: store.root, data: data, pages: pages)
        return url
    }

    private static func render(_ pages: [PageSpec]) throws -> Data {
        var backgrounds: [PaperTemplate: PDFDocument] = [:]
        for spec in pages where backgrounds[spec.template] == nil {
            backgrounds[spec.template] = PDFDocument(data: TemplateRenderer.pdfData(for: spec.template))
        }
        let firstSize = pages.first?.template.size.points ?? PaperSize.a4.points
        let renderer = UIGraphicsPDFRenderer(bounds: CGRect(origin: .zero, size: firstSize))
        let base = renderer.pdfData { context in
            for spec in pages {
                let bounds = CGRect(origin: .zero, size: spec.template.size.points)
                context.beginPage(withBounds: bounds, pageInfo: [:])
                let cg = context.cgContext
                if let background = backgrounds[spec.template]?.page(at: 0)?.pageRef {
                    // The renderer's context has a top-left origin; a PDF page draws bottom-up.
                    cg.saveGState()
                    cg.translateBy(x: 0, y: bounds.height)
                    cg.scaleBy(x: 1, y: -1)
                    cg.drawPDFPage(background)
                    cg.restoreGState()
                }
                let ink = spec.template.color.isDark ? UIColor.white : UIColor(red: 0.09, green: 0.1, blue: 0.13, alpha: 1)
                for line in spec.typed {
                    (line.text as NSString).draw(at: line.origin, withAttributes: [.font: line.font, .foregroundColor: ink])
                }
            }
        }
        guard let pdf = PDFDocument(data: base), pdf.pageCount == pages.count else { throw ZenithError.invalidPDF }
        var geometries: [PageGeometry] = []
        for (index, spec) in pages.enumerated() {
            guard let page = pdf.page(at: index) else { throw ZenithError.invalidPDF }
            let geometry = PageGeometry(page: page)
            geometries.append(geometry)
            for box in spec.textBoxes {
                let bounds = TextBoxCodec.fittedBounds(text: box.text, style: box.style, topLeft: geometry.toPDF(box.topLeft),
                                                       width: box.width)
                page.addAnnotation(TextBoxCodec.makeAnnotation(text: box.text, style: box.style, bounds: bounds))
            }
            for picture in spec.pictures {
                guard let image = symbolImage(picture.symbol), image.size.width > 0,
                      let data = ImageAnnotation.preparedData(for: image) else { continue }
                let height = picture.width * image.size.height / image.size.width
                let rect = CGRect(origin: picture.origin, size: CGSize(width: picture.width, height: height))
                page.addAnnotation(ImageAnnotation(bounds: geometry.rectToPDF(rect), imageData: data))
            }
        }
        guard let live = pdf.dataRepresentation() else { throw ZenithError.invalidPDF }
        let snapshot = NoteSnapshot(pdfData: live, drawings: pages.map { PKDrawing(strokes: $0.strokes) },
                                    geometries: geometries, pageCount: pages.count, noteID: UUID().uuidString)
        return try NoteWriter.render(snapshot)
    }

    /// The index the indexer would write, with the handwriting's words known instead of recognised. Each page's
    /// inkHash is taken from the ink as the file reads back, so opening the note never sends it to Vision.
    private static func writeIndex(note: URL, root: URL, data: Data, pages: [PageSpec]) throws {
        guard let pdf = PDFDocument(data: data) else { throw ZenithError.invalidPDF }
        let read = NoteReader.extract(from: pdf)
        let texts = (0..<pdf.pageCount).map { index -> PageText in
            let drawing = read.indices.contains(index) ? read[index].drawing : PKDrawing()
            return PageText(typed: pdf.page(at: index)?.string ?? "",
                            words: pages.indices.contains(index) ? pages[index].words : [],
                            inkHash: SearchIndex.inkHash(of: drawing))
        }
        try SearchIndex(pages: texts).save(note: note, libraryRoot: root)
    }

    /// An SF Symbol on a light card, as a photo placed on the page would look.
    private static func symbolImage(_ name: String) -> UIImage? {
        let configuration = UIImage.SymbolConfiguration(pointSize: 96, weight: .light)
        guard let symbol = UIImage(systemName: name, withConfiguration: configuration)?
            .withTintColor(blue, renderingMode: .alwaysOriginal) else { return nil }
        let size = CGSize(width: symbol.size.width + 48, height: symbol.size.height + 48)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 2
        format.opaque = true
        return UIGraphicsImageRenderer(size: size, format: format).image { context in
            UIColor(red: 0.93, green: 0.95, blue: 0.98, alpha: 1).setFill()
            context.fill(CGRect(origin: .zero, size: size))
            symbol.draw(in: CGRect(x: 24, y: 24, width: symbol.size.width, height: symbol.size.height))
        }
    }

    private static func star(center: CGPoint, radius: CGFloat) -> [CGPoint] {
        (0..<10).map { index in
            let angle = -CGFloat.pi / 2 + CGFloat(index) * .pi / 5
            let r = index % 2 == 0 ? radius : radius * 0.45
            return CGPoint(x: center.x + r * cos(angle), y: center.y + r * sin(angle))
        }
    }

    /// A soft two-note tone with a fade at each end, AAC in an .m4a file, like a recording the app made.
    private static func writeTone(to url: URL, seconds: TimeInterval) throws {
        try FileStore.createFolder(url.deletingLastPathComponent())
        let rate = 44_100.0
        let settings: [String: Any] = [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: rate,
                                       AVNumberOfChannelsKey: 1]
        let file = try AVAudioFile(forWriting: url, settings: settings, commonFormat: .pcmFormatFloat32,
                                   interleaved: false)
        let chunk: AVAudioFrameCount = 4410
        guard let buffer = AVAudioPCMBuffer(pcmFormat: file.processingFormat, frameCapacity: chunk),
              let channel = buffer.floatChannelData?[0] else {
            throw ZenithError.cannotSave(url, reason: "No audio buffer")
        }
        let total = Int(rate * seconds)
        var frame = 0
        while frame < total {
            let count = min(Int(chunk), total - frame)
            for offset in 0..<count {
                let t = Double(frame + offset) / rate
                let envelope = min(1, t * 4, (seconds - t) * 4)
                let value = 0.08 * envelope * (sin(2 * .pi * 220 * t) + 0.5 * sin(2 * .pi * 330 * t))
                channel[offset] = Float(value)
            }
            buffer.frameLength = AVAudioFrameCount(count)
            try file.write(from: buffer)
            frame += count
        }
    }
}
