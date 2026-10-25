import AEyeCore
import SwiftUI

/// Shown while an agent works on an answer: a progress ring with a percentage and a line telling the user to keep
/// the camera on the thing. The percentage follows the server's recent typical answer time (`typical_ms`), so
/// it is an honest estimate, and it never reaches 100% before the answer arrives.
struct WaitCard: View {
    let wait: Wait

    private static let ringSide: CGFloat = 44

    var body: some View {
        TimelineView(.animation(minimumInterval: 1.0 / 30)) { context in
            let elapsed = context.date.timeIntervalSince1970 * 1000 - wait.startedAtMs
            let progress = waitProgress(elapsedMs: elapsed, typicalMs: wait.typicalMs)
            let copy = copy(elapsedMs: elapsed)

            HStack(spacing: 14) {
                ring(progress: progress)
                VStack(alignment: .leading, spacing: 3) {
                    Text(copy.eyebrow)
                        .font(.system(.caption2, design: .monospaced).weight(.bold))
                        .tracking(1.2)
                        .foregroundStyle(accent)
                    Text(copy.line)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(.white)
                        .lineLimit(2)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
            .padding(.horizontal, 14)
            .padding(.vertical, 12)
            .callGlass(RoundedRectangle(cornerRadius: 20, style: .continuous))
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(copy.eyebrow.capitalized). \(copy.line)")
            .accessibilityValue("\(percent(progress)) percent")
        }
    }

    private func ring(progress: Double) -> some View {
        ZStack {
            Circle()
                .stroke(Color.white.opacity(0.15), lineWidth: 3)
            Circle()
                .trim(from: 0, to: progress)
                .stroke(accent, style: StrokeStyle(lineWidth: 3, lineCap: .round))
                .rotationEffect(.degrees(-90))
            Text("\(percent(progress))%")
                .font(.system(size: 12, weight: .bold, design: .monospaced))
                .monospacedDigit()
                .foregroundStyle(.white)
        }
        .frame(width: Self.ringSide, height: Self.ringSide)
    }

    private var accent: Color {
        wait.agent == .shopper ? Palette.shopper : Palette.helper
    }

    private func percent(_ progress: Double) -> Int {
        Int((progress * 100).rounded(.down))
    }

    private func copy(elapsedMs: Double) -> (eyebrow: String, line: String) {
        if wait.agent == .shopper {
            return ("SHOPPER", "Finding parts that fit…")
        }
        let isSlow = elapsedMs > Double(wait.typicalMs ?? Int(Tuning.defaultTypicalWaitMs))
        switch wait.stage {
        case .received:
            return ("GOT IT", "Hold the camera steady…")
        case .transcribing:
            return ("LISTENING", "Catching your words…")
        case .backup:
            return ("LOOKING CLOSER", "Taking a bit longer. Keep holding.")
        case .thinking, .speaking, .done, .failed, .cancelled, .expert, .expertDone:
            return ("LOOKING", isSlow ? "Almost there. Keep holding." : "Hold the camera steady…")
        }
    }
}
