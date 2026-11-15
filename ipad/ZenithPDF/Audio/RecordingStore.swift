import Foundation

struct Recording: Codable, Identifiable, Hashable, Sendable {
    let id: UUID
    var fileName: String              // "<id>.m4a" inside the sidecar audio folder
    var startedAt: Date
    var duration: TimeInterval
    var title: String                 // "Recording 1"
}

/// Recording, recordings.json.
enum RecordingStore {
    static func load(note: URL, libraryRoot: URL?) -> [Recording] {
        []
    }

    static func save(_ recordings: [Recording], note: URL, libraryRoot: URL?) throws {
    }

    static func fileURL(for recording: Recording, note: URL, libraryRoot: URL?) -> URL {
        SidecarLayout.audioFolder(forNote: note, libraryRoot: libraryRoot)
            .appendingPathComponent(recording.fileName, isDirectory: false)
    }
}
