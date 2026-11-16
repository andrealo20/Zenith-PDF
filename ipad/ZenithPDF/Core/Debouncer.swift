import Foundation

/// Runs the last scheduled action once, `seconds` after the last call to schedule.
@MainActor final class Debouncer {
    private let seconds: Double
    private var pending: (@MainActor () -> Void)?
    private var timer: Task<Void, Never>?

    init(seconds: Double) {
        self.seconds = seconds
    }

    /// Replaces a pending action.
    func schedule(_ action: @escaping @MainActor () -> Void) {
        pending = action
        timer?.cancel()
        let nanoseconds = UInt64(max(seconds, 0) * 1_000_000_000)
        timer = Task { @MainActor [weak self] in
            try? await Task.sleep(nanoseconds: nanoseconds)
            guard !Task.isCancelled else { return }
            self?.flush()
        }
    }

    /// Runs the pending action now, if any.
    func flush() {
        timer?.cancel()
        timer = nil
        guard let action = pending else { return }
        pending = nil
        action()
    }

    func cancel() {
        timer?.cancel()
        timer = nil
        pending = nil
    }
}
