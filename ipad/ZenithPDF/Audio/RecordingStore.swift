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
    /// Empty when the file is missing or unreadable.
    static func load(note: URL, libraryRoot: URL?) -> [Recording] {
        let url = SidecarLayout.recordings(forNote: note, libraryRoot: libraryRoot)
        guard let data = try? FileStore.read(url), !data.isEmpty else { return [] }
        do {
            return try decoder().decode([Recording].self, from: data)
        } catch {
            log.error("recordings.json unreadable: \(error.localizedDescription, privacy: .public)")
            return []
        }
    }

    static func save(_ recordings: [Recording], note: URL, libraryRoot: URL?) throws {
        let url = SidecarLayout.recordings(forNote: note, libraryRoot: libraryRoot)
        try FileStore.createFolder(url.deletingLastPathComponent())
        try FileStore.replace(url, with: try encoder().encode(recordings))
    }

    static func fileURL(for recording: Recording, note: URL, libraryRoot: URL?) -> URL {
        SidecarLayout.audioFolder(forNote: note, libraryRoot: libraryRoot)
            .appendingPathComponent(recording.fileName, isDirectory: false)
    }

    // ISO 8601 with milliseconds: stroke offsets are measured from startedAt, so whole seconds would shift
    // the replay by up to a second. Dates without fractional seconds still decode.
    private static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        encoder.dateEncodingStrategy = .custom { date, encoder in
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            var container = encoder.singleValueContainer()
            try container.encode(formatter.string(from: date))
        }
        return encoder
    }

    private static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { decoder in
            let container = try decoder.singleValueContainer()
            let text = try container.decode(String.self)
            let formatter = ISO8601DateFormatter()
            formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = formatter.date(from: text) { return date }
            formatter.formatOptions = [.withInternetDateTime]
            if let date = formatter.date(from: text) { return date }
            throw DecodingError.dataCorruptedError(in: container, debugDescription: "Not an ISO 8601 date: \(text)")
        }
        return decoder
    }
}
