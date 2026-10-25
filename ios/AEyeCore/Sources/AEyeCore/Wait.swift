import Foundation

/// Where a user turn is on the server (`v2_turn.stage` in `contracts/schema.json`).
public enum TurnStage: String, Codable {
    case received, transcribing, thinking, backup, speaking, done, failed, cancelled
    /// A stronger model is checking the answer (the Expert badge). Sent beside the normal stages, not instead.
    case expert
    case expertDone = "expert_done"

    /// The agent has not answered yet: the user should keep the camera on the thing and wait.
    public var isWaiting: Bool {
        switch self {
        case .received, .transcribing, .thinking, .backup: return true
        case .speaking, .done, .failed, .cancelled, .expert, .expertDone: return false
        }
    }

    /// Expert stages only move the badge; they leave the wait card alone.
    public var isExpertSignal: Bool {
        self == .expert || self == .expertDone
    }
}

/// An agent is working on an answer. The call screen shows a scanning card with a percentage while this is set.
public struct Wait: Equatable {
    /// nil when the wait came from `agent_state: thinking` alone (no `turn` message, for example the shopper).
    public let turnId: String?
    public let agent: Agent
    public let stage: TurnStage
    /// Client clock (`CallController.nowMs()`), set when the wait began, so server clock skew does not matter.
    public let startedAtMs: Double
    /// Server's median of recent turns, end of speech to first spoken text. nil until it has one.
    public let typicalMs: Int?

    public init(turnId: String?, agent: Agent, stage: TurnStage, startedAtMs: Double, typicalMs: Int?) {
        self.turnId = turnId
        self.agent = agent
        self.stage = stage
        self.startedAtMs = startedAtMs
        self.typicalMs = typicalMs
    }
}

/// Share of the wait that has passed, 0 to just under 1. Linear to 0.9 at the typical time, then creeps towards
/// 0.99 so a slow turn keeps moving instead of sitting at 100% looking stuck.
public func waitProgress(elapsedMs: Double, typicalMs: Int?) -> Double {
    let typical = typicalMs.flatMap { $0 > 0 ? Double($0) : nil } ?? Tuning.defaultTypicalWaitMs
    let elapsed = max(0, elapsedMs)
    if elapsed <= typical {
        return 0.9 * elapsed / typical
    }
    return 0.9 + 0.09 * (1 - exp(-(elapsed - typical) / typical))
}
