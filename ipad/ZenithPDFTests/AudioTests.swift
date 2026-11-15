import AVFoundation
import PDFKit
import PencilKit
import UIKit
import XCTest
@testable import ZenithPDF

/// Audio sync. Nothing here records: playback uses a tone generated with AVAudioFile, so no microphone is needed.
final class AudioTests: XCTestCase {
    private struct ToneError: Error {}

    private let start = Date(timeIntervalSince1970: 1_800_000_000.25)

    private func recording(duration: TimeInterval = 10, startedAt: Date? = nil, title: String = "Recording 1") -> Recording {
        let id = UUID()
        return Recording(id: id, fileName: id.uuidString + ".m4a", startedAt: startedAt ?? start,
                         duration: duration, title: title)
    }

    /// A horizontal stroke from x 50 to 150 at `y`, started at `date`.
    private func line(y: CGFloat, at date: Date, color: UIColor = .black) -> PKStroke {
        TestSupport.stroke((0...10).map { CGPoint(x: 50 + CGFloat($0) * 10, y: y) }, color: color, date: date)
    }

    private func alphas(_ drawing: PKDrawing?) -> [CGFloat] {
        (drawing?.strokes ?? []).map { $0.ink.color.cgColor.alpha }
    }

    private func assertAlphas(_ drawing: PKDrawing?, _ expected: [CGFloat], file: StaticString = #filePath, line: UInt = #line) {
        let actual = alphas(drawing)
        XCTAssertEqual(actual.count, expected.count, "stroke count", file: file, line: line)
        for (a, e) in zip(actual, expected) {
            XCTAssertEqual(a, e, accuracy: 0.02, "alphas \(actual)", file: file, line: line)
        }
    }

    @MainActor private func openNote(_ name: String = #function) async throws -> (NoteDocument, EditorViewController) {
        let folder = TestSupport.temporaryFolder(name)
        let url = TestSupport.writeNote(TestSupport.pdf(pages: 1), named: "Lecture", in: folder)
        let document = try await NoteDocument.open(url, libraryRoot: folder)
        return (document, EditorViewController(document: document))
    }

    /// A 440 Hz tone, AAC, 44.1 kHz mono, like a real recording.
    private func writeTone(to url: URL, seconds: Double) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        let settings: [String: Any] = [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 44_100.0,
            AVNumberOfChannelsKey: 1,
        ]
        let file = try AVAudioFile(forWriting: url, settings: settings)
        let format = file.processingFormat
        let frames = AVAudioFrameCount(seconds * format.sampleRate)
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: frames),
              let samples = buffer.floatChannelData?[0] else { throw ToneError() }
        for index in 0..<Int(frames) {
            samples[index] = Float(sin(2 * Double.pi * 440 * Double(index) / format.sampleRate) * 0.2)
        }
        buffer.frameLength = frames
        try file.write(from: buffer)
    }

    func testRecordingsRoundTrip() throws {
        let folder = TestSupport.temporaryFolder()
        let note = folder.appendingPathComponent("Physics.pdf", isDirectory: false)
        XCTAssertEqual(RecordingStore.load(note: note, libraryRoot: folder), [])

        let first = recording(duration: 12.5)
        let second = recording(duration: 3, startedAt: start.addingTimeInterval(60.125), title: "Lab")
        try RecordingStore.save([first, second], note: note, libraryRoot: folder)
        let file = SidecarLayout.recordings(forNote: note, libraryRoot: folder)
        XCTAssertTrue(FileManager.default.fileExists(atPath: file.path))

        let loaded = RecordingStore.load(note: note, libraryRoot: folder)
        XCTAssertEqual(loaded.count, 2)
        for (read, written) in zip(loaded, [first, second]) {
            XCTAssertEqual(read.id, written.id)
            XCTAssertEqual(read.fileName, written.fileName)
            XCTAssertEqual(read.title, written.title)
            XCTAssertEqual(read.duration, written.duration, accuracy: 0.0001)
            // Sub-second start times survive, or every stroke offset would shift.
            XCTAssertEqual(read.startedAt.timeIntervalSince1970, written.startedAt.timeIntervalSince1970, accuracy: 0.001)
        }
        XCTAssertEqual(RecordingStore.fileURL(for: first, note: note, libraryRoot: folder),
                       SidecarLayout.audioFolder(forNote: note, libraryRoot: folder).appendingPathComponent(first.fileName))

        try Data("not json".utf8).write(to: file)
        XCTAssertEqual(RecordingStore.load(note: note, libraryRoot: folder), [])
    }

    func testOffsetsInsideWindowOnly() {
        let timeline = ReplayTimeline(recording: recording(duration: 10))
        XCTAssertNil(timeline.offset(of: line(y: 10, at: start.addingTimeInterval(-1))))
        XCTAssertEqual(timeline.offset(of: line(y: 10, at: start)) ?? -1, 0, accuracy: 0.001)
        XCTAssertEqual(timeline.offset(of: line(y: 10, at: start.addingTimeInterval(2.5))) ?? -1, 2.5, accuracy: 0.001)
        XCTAssertEqual(timeline.offset(of: line(y: 10, at: start.addingTimeInterval(10))) ?? -1, 10, accuracy: 0.001)
        XCTAssertNil(timeline.offset(of: line(y: 10, at: start.addingTimeInterval(10.5))))
        XCTAssertNil(timeline.offset(of: line(y: 10, at: .distantPast)))
    }

    func testPresentationFadesLaterStrokes() {
        let timeline = ReplayTimeline(recording: recording(duration: 10))
        let drawing = PKDrawing(strokes: [
            line(y: 10, at: start.addingTimeInterval(1)),
            line(y: 20, at: start.addingTimeInterval(5), color: .red),
            line(y: 30, at: start.addingTimeInterval(-5)),
            line(y: 40, at: start.addingTimeInterval(3)),
        ])

        let shown = timeline.presentation(of: drawing, at: 3)
        assertAlphas(shown, [1, 0.15, 1, 1])
        for (a, b) in zip(shown.strokes, drawing.strokes) {
            XCTAssertEqual(a.path.creationDate, b.path.creationDate)
            XCTAssertEqual(a.path.count, b.path.count)
            XCTAssertEqual(a.ink.inkType, b.ink.inkType)
            XCTAssertEqual(a.path.first?.location, b.path.first?.location)
        }
        assertAlphas(timeline.presentation(of: drawing, at: 0), [0.15, 0.15, 1, 0.15])
        assertAlphas(timeline.presentation(of: drawing, at: 10), [1, 1, 1, 1])
    }

    func testSeekTimeNearestStroke() {
        let timeline = ReplayTimeline(recording: recording(duration: 10))
        let drawing = PKDrawing(strokes: [
            line(y: 100, at: start.addingTimeInterval(4)),
            line(y: 200, at: start.addingTimeInterval(7)),
            line(y: 150, at: start.addingTimeInterval(-3)),
            line(y: 300, at: start.addingTimeInterval(0.5)),
        ])
        XCTAssertEqual(timeline.seekTime(in: drawing, near: CGPoint(x: 100, y: 104), tolerance: 8) ?? -1, 3, accuracy: 0.001)
        XCTAssertEqual(timeline.seekTime(in: drawing, near: CGPoint(x: 120, y: 196), tolerance: 8) ?? -1, 6, accuracy: 0.001)
        XCTAssertEqual(timeline.seekTime(in: drawing, near: CGPoint(x: 100, y: 302), tolerance: 8) ?? -1, 0, accuracy: 0.001)
        // Only a stroke written outside the recording is there.
        XCTAssertNil(timeline.seekTime(in: drawing, near: CGPoint(x: 100, y: 150), tolerance: 8))
        XCTAssertNil(timeline.seekTime(in: drawing, near: CGPoint(x: 100, y: 120), tolerance: 8))

        let close = PKDrawing(strokes: [
            line(y: 100, at: start.addingTimeInterval(4)),
            line(y: 106, at: start.addingTimeInterval(8)),
        ])
        XCTAssertEqual(timeline.seekTime(in: close, near: CGPoint(x: 100, y: 104.5), tolerance: 8) ?? -1, 7, accuracy: 0.001)
    }

    @MainActor func testDeleteRemovesFile() async throws {
        let (document, editor) = try await openNote()
        let a = recording()
        let b = recording(title: "Recording 2")
        let urlA = RecordingStore.fileURL(for: a, note: document.url, libraryRoot: document.libraryRoot)
        let urlB = RecordingStore.fileURL(for: b, note: document.url, libraryRoot: document.libraryRoot)
        try FileManager.default.createDirectory(at: urlA.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data([0, 1, 2]).write(to: urlA)
        try Data([3, 4, 5]).write(to: urlB)
        try RecordingStore.save([a, b], note: document.url, libraryRoot: document.libraryRoot)

        let audio = AudioController(document: document, editor: editor)
        XCTAssertEqual(audio.recordings.map(\.id), [a.id, b.id])

        audio.delete(a)
        XCTAssertFalse(FileManager.default.fileExists(atPath: urlA.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: urlB.path))
        XCTAssertEqual(audio.recordings.map(\.id), [b.id])
        XCTAssertEqual(RecordingStore.load(note: document.url, libraryRoot: document.libraryRoot).map(\.id), [b.id])

        audio.rename(b, to: "  Lab  ")
        audio.rename(b, to: "   ")
        XCTAssertEqual(audio.recordings.first?.title, "Lab")
        XCTAssertEqual(RecordingStore.load(note: document.url, libraryRoot: document.libraryRoot).first?.title, "Lab")
    }

    @MainActor func testPlaybackDrivesPresentation() async throws {
        let (document, editor) = try await openNote()
        let startedAt = Date().addingTimeInterval(-60)
        let tone = recording(duration: 1, startedAt: startedAt)
        try writeTone(to: RecordingStore.fileURL(for: tone, note: document.url, libraryRoot: document.libraryRoot), seconds: 1)
        try RecordingStore.save([tone], note: document.url, libraryRoot: document.libraryRoot)

        let pageID = document.pageID(at: 0)
        document.setDrawing(PKDrawing(strokes: [
            line(y: 100, at: startedAt.addingTimeInterval(0.2)),
            line(y: 200, at: startedAt.addingTimeInterval(0.8)),
            line(y: 300, at: startedAt.addingTimeInterval(-10)),
        ]), forPageID: pageID, undoable: false)
        XCTAssertEqual(document.drawing(forPageID: pageID).strokes.count, 3)

        let audio = AudioController(document: document, editor: editor)
        audio.replayInk = true
        XCTAssertEqual(audio.recordings.map(\.id), [tone.id])
        XCTAssertNil(editor.presentationProvider)

        audio.play(tone)
        XCTAssertEqual(audio.current?.id, tone.id)
        XCTAssertTrue(editor.isPresenting)
        assertAlphas(editor.presentationProvider?(pageID), [0.15, 0.15, 1])

        audio.seek(to: 0.5)
        XCTAssertEqual(audio.currentTime, 0.5, accuracy: 0.001)
        assertAlphas(editor.presentationProvider?(pageID), [1, 0.15, 1])

        // A tap on the second stroke jumps to its offset minus 1 s, clamped at 0.
        editor.presentationTapHandler?(0, CGPoint(x: 100, y: 201))
        XCTAssertEqual(audio.currentTime, 0, accuracy: 0.001)
        assertAlphas(editor.presentationProvider?(pageID), [0.15, 0.15, 1])

        audio.seek(to: 0.9)
        assertAlphas(editor.presentationProvider?(pageID), [1, 1, 1])

        audio.pause()
        XCTAssertFalse(audio.isPlaying)
        XCTAssertTrue(editor.isPresenting, "a paused replay keeps showing the page as it was")

        audio.replayInk = false
        XCTAssertFalse(editor.isPresenting)
        audio.replayInk = true
        XCTAssertTrue(editor.isPresenting)

        // Real time: only where the simulator has an audio output to play through.
        audio.seek(to: 0)
        audio.play(tone)
        if audio.isPlaying {
            try await Task.sleep(nanoseconds: 400_000_000)
            if audio.isPlaying { XCTAssertGreaterThan(audio.currentTime, 0.1) }
        }

        audio.stopAll()
        XCTAssertNil(editor.presentationProvider)
        XCTAssertNil(editor.presentationTapHandler)
        XCTAssertNil(audio.current)
        XCTAssertFalse(audio.isPlaying)
        // The replay never touches the note's own ink.
        assertAlphas(document.drawing(forPageID: pageID), [1, 1, 1])
    }
}
