import SwiftUI

/// SwiftUI bar.
struct AudioBar: View {
    private let audio: AudioController

    init(audio: AudioController) {
        self.audio = audio
    }

    var body: some View {
        EmptyView()
    }
}
