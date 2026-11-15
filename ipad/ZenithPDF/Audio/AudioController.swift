import AVFoundation
import Foundation
import Observation
import PencilKit

/// Record, play, seek, replay wiring.
@MainActor @Observable final class AudioController {
    private let document: NoteDocument
    private let editor: EditorViewController

    private(set) var recordings: [Recording] = []
    private(set) var isRecording: Bool = false
    private(set) var recordingElapsed: TimeInterval = 0
    private(set) var level: Float = 0             // 0...1, for the meter
    private(set) var current: Recording? = nil
    private(set) var isPlaying: Bool = false
    private(set) var currentTime: TimeInterval = 0
    var replayInk: Bool = (UserDefaults.standard.object(forKey: SettingKey.replayInk) as? Bool) ?? true {
        didSet {
            UserDefaults.standard.set(replayInk, forKey: SettingKey.replayInk)
            syncPresentation()
        }
    }
    var permissionDenied: Bool = false

    @ObservationIgnored private var recorder: AVAudioRecorder? = nil
    @ObservationIgnored private var pending: Recording? = nil
    @ObservationIgnored private var player: AVAudioPlayer? = nil
    @ObservationIgnored private var timeline: ReplayTimeline? = nil
    @ObservationIgnored private var tickTask: Task<Void, Never>? = nil
    /// True while playing or paused on a recording: the pages then show the replay (when replayInk is on).
    @ObservationIgnored private var presentationWanted = false
    @ObservationIgnored private var presenting = false
    /// Recorded stroke offsets of every page, sorted, taken when the replay starts showing.
    @ObservationIgnored private var offsets: [TimeInterval] = []
    @ObservationIgnored private var shownCount = 0

    init(document: NoteDocument, editor: EditorViewController) {
        self.document = document
        self.editor = editor
        recordings = RecordingStore.load(note: document.url, libraryRoot: document.libraryRoot)
    }

    func startRecording() async {
        guard !isRecording else { return }
        let granted = await AVAudioApplication.requestRecordPermission()
        guard granted else {
            permissionDenied = true
            return
        }
        guard !isRecording else { return }
        permissionDenied = false
        if isPlaying { pause() }
        presentationWanted = false
        syncPresentation()

        let id = UUID()
        var recording = Recording(id: id, fileName: id.uuidString + ".m4a", startedAt: Date(), duration: 0,
                                  title: tr("Recording %lld", recordings.count + 1))
        let url = RecordingStore.fileURL(for: recording, note: document.url, libraryRoot: document.libraryRoot)
        do {
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .default, options: [.defaultToSpeaker, .allowBluetoothA2DP])
            try session.setActive(true)
            let newRecorder = try AVAudioRecorder(url: url, settings: Self.recordSettings)
            newRecorder.isMeteringEnabled = true
            guard newRecorder.record() else {
                log.error("The recorder did not start")
                deactivateSession()
                return
            }
            recording.startedAt = Date()
            recorder = newRecorder
            pending = recording
            recordingElapsed = 0
            level = 0
            isRecording = true
            startTicking()
        } catch {
            log.error("Recording failed to start: \(error.localizedDescription, privacy: .public)")
            deactivateSession()
        }
    }

    func stopRecording() {
        guard isRecording, let recorder, var recording = pending else { return }
        let elapsed = recorder.currentTime
        recording.duration = elapsed > 0 ? elapsed : Date().timeIntervalSince(recording.startedAt)
        recorder.stop()
        self.recorder = nil
        pending = nil
        isRecording = false
        recordingElapsed = 0
        level = 0
        recordings.append(recording)
        persist()
        deactivateSession()
    }

    /// Resumes when `recording` is the one already loaded, else starts it from the beginning.
    func play(_ recording: Recording) {
        if isRecording { stopRecording() }
        if current?.id != recording.id || player == nil {
            let url = RecordingStore.fileURL(for: recording, note: document.url, libraryRoot: document.libraryRoot)
            let newPlayer: AVAudioPlayer
            do {
                newPlayer = try AVAudioPlayer(contentsOf: url)
            } catch {
                log.error("Cannot play \(recording.fileName, privacy: .public): \(error.localizedDescription, privacy: .public)")
                return
            }
            player?.stop()
            newPlayer.prepareToPlay()
            player = newPlayer
            current = recording
            timeline = ReplayTimeline(recording: recording)
            currentTime = 0
            if presenting { removePresentation() }
        }
        guard let player else { return }
        let session = AVAudioSession.sharedInstance()
        do {
            try session.setCategory(.playback, mode: .spokenAudio)
            try session.setActive(true)
        } catch {
            log.error("Audio session for playback: \(error.localizedDescription, privacy: .public)")
        }
        isPlaying = player.play()
        if !isPlaying { log.error("Playback did not start") }
        presentationWanted = true
        syncPresentation()
        startTicking()
    }

    func pause() {
        guard let player else { return }
        player.pause()
        currentTime = player.currentTime
        isPlaying = false
        syncPresentation()
    }

    func seek(to time: TimeInterval) {
        guard let player, let current else { return }
        let end = player.duration > 0 ? player.duration : current.duration
        let clamped = min(max(time, 0), end)
        player.currentTime = clamped
        currentTime = clamped
        presentationWanted = true
        syncPresentation()
    }

    func rename(_ recording: Recording, to title: String) {
        let trimmed = title.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty, let index = recordings.firstIndex(where: { $0.id == recording.id }) else { return }
        recordings[index].title = trimmed
        if current?.id == recording.id { current?.title = trimmed }
        persist()
    }

    func delete(_ recording: Recording) {
        if current?.id == recording.id { stopPlayback() }
        let url = RecordingStore.fileURL(for: recording, note: document.url, libraryRoot: document.libraryRoot)
        if FileStore.exists(url) {
            do {
                try FileStore.remove(url)
            } catch {
                log.error("Cannot delete \(recording.fileName, privacy: .public): \(error.localizedDescription, privacy: .public)")
            }
        }
        recordings.removeAll { $0.id == recording.id }
        persist()
    }

    /// Stops recording and playback, clears the presentation.
    func stopAll() {
        if isRecording { stopRecording() }
        stopPlayback()
        stopTicking()
        deactivateSession()
    }

    // MARK: - Private

    private static var recordSettings: [String: Any] {
        [
            AVFormatIDKey: Int(kAudioFormatMPEG4AAC),
            AVSampleRateKey: 44_100.0,
            AVNumberOfChannelsKey: 1,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ]
    }

    private func stopPlayback() {
        player?.stop()
        player = nil
        timeline = nil
        current = nil
        isPlaying = false
        currentTime = 0
        presentationWanted = false
        syncPresentation()
    }

    private func persist() {
        do {
            try RecordingStore.save(recordings, note: document.url, libraryRoot: document.libraryRoot)
        } catch {
            log.error("Cannot save recordings.json: \(error.localizedDescription, privacy: .public)")
        }
    }

    private func deactivateSession() {
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func startTicking() {
        guard tickTask == nil else { return }
        tickTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: 100_000_000)
                guard let self else { return }
                self.tick()
            }
        }
    }

    private func stopTicking() {
        tickTask?.cancel()
        tickTask = nil
    }

    /// 10 Hz: the recorder's meter and elapsed time, the playhead and the end of playback.
    private func tick() {
        if isRecording, let recorder {
            recorder.updateMeters()
            recordingElapsed = recorder.currentTime
            level = Self.level(decibels: recorder.averagePower(forChannel: 0))
        }
        if isPlaying, let player {
            if player.isPlaying {
                currentTime = player.currentTime
                syncPresentation()
            } else {
                finishPlayback()
            }
        }
        if !isRecording && !isPlaying { stopTicking() }
    }

    /// The end of the file: back to the start, the pages show the ink again, the recording stays selected.
    private func finishPlayback() {
        isPlaying = false
        currentTime = 0
        player?.currentTime = 0
        presentationWanted = false
        syncPresentation()
    }

    private static func level(decibels: Float) -> Float {
        min(max(powf(10, decibels / 20), 0), 1)
    }

    /// Shows, refreshes or removes the replay so it matches the state. Refreshes only when the number of strokes
    /// past the playhead changed, since that is all a presentation depends on.
    private func syncPresentation() {
        let wanted = presentationWanted && replayInk && timeline != nil
        if wanted && !presenting {
            installPresentation()
        } else if !wanted && presenting {
            removePresentation()
        } else if wanted {
            let count = strokesShown(at: currentTime)
            if count != shownCount {
                shownCount = count
                editor.refreshPresentations()
            }
        }
    }

    private func installPresentation() {
        guard let timeline else { return }
        offsets = document.pages.flatMap { page in page.drawing.strokes.compactMap { timeline.offset(of: $0) } }.sorted()
        shownCount = strokesShown(at: currentTime)
        presenting = true
        editor.presentationTapHandler = { [weak self] index, point in
            self?.seekToStroke(onPage: index, near: point)
        }
        editor.presentationProvider = { [weak self] id in
            self?.presentedDrawing(forPageID: id)
        }
    }

    private func removePresentation() {
        presenting = false
        offsets = []
        shownCount = 0
        editor.presentationTapHandler = nil
        editor.presentationProvider = nil
    }

    private func strokesShown(at time: TimeInterval) -> Int {
        offsets.prefix(while: { $0 <= time }).count
    }

    private func presentedDrawing(forPageID id: UUID) -> PKDrawing {
        let drawing = document.drawing(forPageID: id)
        guard let timeline else { return drawing }
        return timeline.presentation(of: drawing, at: currentTime)
    }

    private func seekToStroke(onPage index: Int, near point: CGPoint) {
        guard let timeline, document.pages.indices.contains(index) else { return }
        if let time = timeline.seekTime(in: document.pages[index].drawing, near: point, tolerance: 8) {
            seek(to: time)
        }
    }
}
