import AEyeCore
import SwiftUI

/// Small status dot for the call pill. Shows the agent that is not idle (the shopper wins if both are):
/// a pulsing dot while it speaks, a rotating ring while it thinks, a steady ringed dot while it listens.
/// Idle shows nothing, but the space is kept so the pill does not jump.
struct PresenceDot: View {
    let presence: [Agent: PresenceState]

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private static let side: CGFloat = 16
    private static let pulsePeriod = 0.9
    private static let spinPeriod = 1.0

    private var active: (agent: Agent, state: PresenceState)? {
        for agent in [Agent.shopper, Agent.helper] {
            if let state = presence[agent], state != .idle { return (agent, state) }
        }
        return nil
    }

    var body: some View {
        ZStack {
            if let active {
                TimelineView(.animation(minimumInterval: nil, paused: reduceMotion || active.state == .listening)) { timeline in
                    indicator(agent: active.agent, state: active.state,
                              seconds: timeline.date.timeIntervalSinceReferenceDate)
                }
            }
        }
        .frame(width: Self.side, height: Self.side)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(accessibilityText)
        .accessibilityHidden(active == nil)
    }

    @ViewBuilder
    private func indicator(agent: Agent, state: PresenceState, seconds: Double) -> some View {
        let color = agent == .shopper ? Palette.shopper : Palette.helper
        let wave = 0.5 + 0.5 * sin(seconds * 2 * Double.pi / Self.pulsePeriod)
        let pulse = (state == .speaking && !reduceMotion) ? 1 + 0.35 * wave : 1
        let spin = (state == .thinking && !reduceMotion)
            ? seconds.truncatingRemainder(dividingBy: Self.spinPeriod) / Self.spinPeriod * 360
            : 0

        ZStack {
            Circle()
                .fill(color)
                .frame(width: 8, height: 8)
                .scaleEffect(pulse)
            switch state {
            case .speaking:
                Circle()
                    .fill(color.opacity(0.25))
                    .frame(width: 14, height: 14)
                    .scaleEffect(pulse)
            case .thinking:
                Circle()
                    .trim(from: 0, to: 0.7)
                    .stroke(color, style: StrokeStyle(lineWidth: 2, lineCap: .round))
                    .frame(width: 14, height: 14)
                    .rotationEffect(.degrees(spin))
            case .listening:
                Circle()
                    .stroke(color.opacity(0.45), lineWidth: 1.5)
                    .frame(width: 14, height: 14)
            case .idle:
                EmptyView()
            }
        }
    }

    private var accessibilityText: String {
        guard let active else { return "" }
        let who = active.agent == .shopper ? "Shopper" : "Helper"
        switch active.state {
        case .speaking: return "\(who) is speaking"
        case .thinking: return "\(who) is thinking"
        case .listening: return "\(who) is listening"
        case .idle: return ""
        }
    }
}
