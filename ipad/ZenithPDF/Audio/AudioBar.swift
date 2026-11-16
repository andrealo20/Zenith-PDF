import SwiftUI
import UIKit

/// SwiftUI bar at the bottom of the editor: the recording in progress, or the note's recordings with playback.
/// Shows nothing while the note has no recordings and nothing is being recorded.
struct AudioBar: View {
    private let audio: AudioController
    @Environment(\.openURL) private var openURL
    @State private var renaming: Recording?
    @State private var newTitle = ""
    @State private var deleting: Recording?

    init(audio: AudioController) {
        self.audio = audio
    }

    var body: some View {
        VStack(spacing: 8) {
            if audio.permissionDenied {
                permissionRow
            }
            if audio.isRecording {
                recordingRow
            } else if let selected {
                playbackRow(selected)
            }
        }
        .padding(.horizontal, audio.isRecording || selected != nil || audio.permissionDenied ? 14 : 0)
        .padding(.vertical, audio.isRecording || selected != nil || audio.permissionDenied ? 10 : 0)
        .background {
            if audio.isRecording || selected != nil || audio.permissionDenied {
                RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Folio.raised)
                    .shadow(color: Color.black.opacity(0.12), radius: 9, x: 0, y: 6)
            }
        }
        .accessibilityIdentifier("audioBar")
        .alert("Rename", isPresented: Binding(get: { renaming != nil }, set: { if !$0 { renaming = nil } })) {
            TextField("Name", text: $newTitle)
            Button("Cancel", role: .cancel) { renaming = nil }
            Button("Rename") {
                if let renaming { audio.rename(renaming, to: newTitle) }
                renaming = nil
            }
        }
        .confirmationDialog("Delete Recording", isPresented: Binding(get: { deleting != nil }, set: { if !$0 { deleting = nil } }),
                            titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let deleting { audio.delete(deleting) }
                deleting = nil
            }
            Button("Cancel", role: .cancel) { deleting = nil }
        } message: {
            Text("This cannot be undone.")
        }
    }

    /// The recording the play button acts on: the one loaded, else the newest.
    private var selected: Recording? {
        audio.current ?? audio.recordings.last
    }

    private var permissionRow: some View {
        HStack(spacing: 12) {
            Image(systemName: "mic.slash").foregroundStyle(Folio.danger)
            Text("Microphone access is off. Turn it on in Settings to record.")
                .font(.callout)
                .foregroundStyle(Folio.text)
            Spacer(minLength: 0)
            Button("Settings") {
                if let url = URL(string: UIApplication.openSettingsURLString) { openURL(url) }
            }
            Button("Done") { audio.permissionDenied = false }
        }
    }

    private var recordingRow: some View {
        HStack(spacing: 12) {
            Circle().fill(Folio.danger).frame(width: 10, height: 10)
            Text(verbatim: Self.clock(audio.recordingElapsed))
                .font(.body.monospacedDigit())
                .foregroundStyle(Folio.text)
            RoundedRectangle(cornerRadius: 2, style: .continuous)
                .fill(Folio.border)
                .frame(width: 96, height: 6)
                .overlay(alignment: .leading) {
                    RoundedRectangle(cornerRadius: 2, style: .continuous)
                        .fill(Folio.danger)
                        .frame(width: 96 * CGFloat(audio.level), height: 6)
                }
                .accessibilityHidden(true)
            Spacer(minLength: 0)
            Button {
                audio.stopRecording()
            } label: {
                Label("Stop", systemImage: "stop.fill")
            }
            .accessibilityIdentifier("audio.stop")
        }
    }

    private func playbackRow(_ recording: Recording) -> some View {
        let duration = max(recording.duration, 0.1)
        return HStack(spacing: 12) {
            Button {
                if audio.isPlaying { audio.pause() } else { audio.play(recording) }
            } label: {
                Image(systemName: audio.isPlaying ? "pause.fill" : "play.fill")
                    .frame(width: 28, height: 28)
            }
            .accessibilityLabel(audio.isPlaying ? Text("Pause") : Text("Play"))
            .accessibilityIdentifier("audio.play")

            Menu {
                ForEach(audio.recordings) { item in
                    Button(item.title) { audio.play(item) }
                }
            } label: {
                Text(recording.title)
                    .lineLimit(1)
                    .foregroundStyle(Folio.text)
            }
            .accessibilityLabel(Text("Recordings"))

            Text(verbatim: Self.clock(audio.current == nil ? 0 : audio.currentTime))
                .font(.caption.monospacedDigit())
                .foregroundStyle(Folio.textMuted)
            Slider(value: Binding(get: { min(audio.currentTime, duration) }, set: { audio.seek(to: $0) }),
                   in: 0...duration)
                .frame(minWidth: 120)
                .disabled(audio.current?.id != recording.id)
            Text(verbatim: Self.clock(recording.duration))
                .font(.caption.monospacedDigit())
                .foregroundStyle(Folio.textMuted)

            Toggle(isOn: Binding(get: { audio.replayInk }, set: { audio.replayInk = $0 })) {
                Label("Replay Writing", systemImage: "scribble.variable")
            }
            .toggleStyle(.button)
            .labelStyle(.iconOnly)

            Menu {
                Button("Rename") {
                    newTitle = recording.title
                    renaming = recording
                }
                Button("Delete Recording", role: .destructive) { deleting = recording }
            } label: {
                Image(systemName: "ellipsis.circle")
            }
            .accessibilityLabel(Text("More"))

            if audio.current != nil {
                Button {
                    audio.stopAll()
                } label: {
                    Image(systemName: "xmark")
                }
                .accessibilityLabel(Text("Stop"))
            }
        }
        .foregroundStyle(Folio.textMuted)
    }

    /// "m:ss"
    private static func clock(_ time: TimeInterval) -> String {
        let seconds = max(Int(time.rounded(.down)), 0)
        return String(format: "%d:%02d", seconds / 60, seconds % 60)
    }
}
