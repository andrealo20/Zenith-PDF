import Foundation
import Observation

/// Record, play, seek, replay wiring.
@MainActor @Observable final class AudioController {
    private let document: NoteDocument
    private let editor: EditorViewController

    init(document: NoteDocument, editor: EditorViewController) {
        self.document = document
        self.editor = editor
    }

    private(set) var recordings: [Recording] = []
    private(set) var isRecording: Bool = false
    private(set) var recordingElapsed: TimeInterval = 0
    private(set) var level: Float = 0             // 0...1, for the meter
    private(set) var current: Recording? = nil
    private(set) var isPlaying: Bool = false
    private(set) var currentTime: TimeInterval = 0
    var replayInk: Bool = true {                  // SettingKey.replayInk
        didSet {}
    }
    var permissionDenied: Bool = false

    func startRecording() async {
    }

    func stopRecording() {
    }

    func play(_ recording: Recording) {
    }

    func pause() {
    }

    func seek(to time: TimeInterval) {
    }

    func rename(_ recording: Recording, to title: String) {
    }

    func delete(_ recording: Recording) {
    }

    /// Stops recording and playback, clears the presentation.
    func stopAll() {
    }
}
