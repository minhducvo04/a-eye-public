import AEyeCore
import SwiftUI

/// Live mic waveform with what the recogniser has heard so far, so the user can see the app hearing them.
/// Bars light up in the helper colour while the user talks and sit low and grey on room noise.
struct ListeningStrip: View {
    let listener: SpeechIn

    var body: some View {
        let levels = listener.levels
        let heard = listener.interim
        let isTalking = !heard.isEmpty || isVoiceLevel(levels.last ?? 0)

        HStack(spacing: 12) {
            WaveformBars(levels: levels, count: SpeechIn.levelHistoryCount, isActive: isTalking && !listener.isMuted)
                .frame(width: 96, height: 28)

            Text(label(heard: heard, isTalking: isTalking))
                .font(heard.isEmpty ? .system(.footnote, design: .monospaced).weight(.semibold) : .subheadline)
                .foregroundStyle(textColor(heard: heard, isTalking: isTalking))
                .lineLimit(2)
                .frame(maxWidth: .infinity, alignment: .leading)
                .animation(.easeOut(duration: 0.15), value: isTalking)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 10)
        .glass(RoundedRectangle(cornerRadius: 18, style: .continuous))
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(listener.isMuted ? "Microphone off" : (heard.isEmpty ? "Listening" : "Heard: \(heard)"))
    }

    private func label(heard: String, isTalking: Bool) -> String {
        if listener.isMuted { return "MIC OFF" }
        if !heard.isEmpty { return heard }
        return isTalking ? "HEARING YOU…" : "LISTENING"
    }

    private func textColor(heard: String, isTalking: Bool) -> Color {
        if listener.isMuted { return Palette.warn }
        if !heard.isEmpty { return .white }
        return isTalking ? Palette.helper : Color.white.opacity(0.55)
    }
}

/// Mirrored bars, newest on the right. Missing history is drawn as flat bars so the width never changes.
private struct WaveformBars: View {
    let levels: [Double]
    let count: Int
    let isActive: Bool

    private static let barWidth: CGFloat = 2
    private static let minHeight: CGFloat = 2

    var body: some View {
        Canvas { context, size in
            let gap = (size.width - CGFloat(count) * Self.barWidth) / CGFloat(max(1, count - 1))
            let padded = Array(repeating: 0.0, count: max(0, count - levels.count)) + levels.suffix(count)
            let color = isActive ? Palette.helper : Color.white.opacity(0.4)
            for (index, level) in padded.enumerated() {
                let height = Self.minHeight + CGFloat(level) * (size.height - Self.minHeight)
                let rect = CGRect(
                    x: CGFloat(index) * (Self.barWidth + gap),
                    y: (size.height - height) / 2,
                    width: Self.barWidth,
                    height: height
                )
                context.fill(Path(roundedRect: rect, cornerRadius: Self.barWidth / 2), with: .color(color))
            }
        }
        .accessibilityHidden(true)
    }
}
