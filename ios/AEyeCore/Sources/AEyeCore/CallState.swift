import Foundation

public enum LinkStatus: Equatable {
    case connecting, live, reconnecting
}

public struct Caption: Equatable {
    public let agent: Agent
    public let text: String
    public let utteranceId: String

    public init(agent: Agent, text: String, utteranceId: String) {
        self.agent = agent
        self.text = text
        self.utteranceId = utteranceId
    }
}

public enum CallEvent: Equatable {
    case connected
    case disconnected
    case server(ServerMessage)
    case powerOffTapped
    case marketDismissed
}

/// Everything the call screen renders. `reduce` is pure: it returns a new value and never mutates `self`.
public struct CallState: Equatable {
    public var link: LinkStatus
    public var caption: Caption?
    public var presence: [Agent: PresenceState]
    public var powerOffConfirmed: Bool
    public var draw: DrawState
    public var market: MarketResponse?
    public var errorText: String?
    /// Set while an agent works on an answer; nil once it speaks, fails or the link drops.
    public var wait: Wait?
    /// The turn a stronger model is checking (`turn.stage == expert`), shown as the Expert badge; nil otherwise.
    public var expertTurnId: String?

    public init(link: LinkStatus, caption: Caption?, presence: [Agent: PresenceState], powerOffConfirmed: Bool,
                draw: DrawState, market: MarketResponse?, errorText: String?, wait: Wait? = nil,
                expertTurnId: String? = nil) {
        self.link = link
        self.caption = caption
        self.presence = presence
        self.powerOffConfirmed = powerOffConfirmed
        self.draw = draw
        self.market = market
        self.errorText = errorText
        self.wait = wait
        self.expertTurnId = expertTurnId
    }

    public var isExpertChecking: Bool { expertTurnId != nil }

    public static let initial = CallState(
        link: .connecting,
        caption: nil,
        presence: [:],
        powerOffConfirmed: false,
        draw: .empty,
        market: nil,
        errorText: nil
    )

    public func reduce(_ event: CallEvent, nowMs: Double) -> CallState {
        var next = self
        switch event {
        case .connected:
            // A fresh socket is a fresh session on the server: the power-off gate and drawing start over.
            next.link = .live
            next.powerOffConfirmed = false
            next.draw = .empty
            next.presence = [:]
            next.errorText = nil
            next.wait = nil
            next.expertTurnId = nil
        case .disconnected:
            next.link = .reconnecting
            next.wait = nil
            next.expertTurnId = nil
        case .powerOffTapped:
            next.powerOffConfirmed = true
        case .marketDismissed:
            next.market = nil
        case .server(let message):
            switch message {
            case .draw(let agent, let seq, let ops):
                next.draw = draw.applying(agent: agent, seq: seq, ops: ops, nowMs: nowMs)
            case .say(let agent, let utteranceId, let text):
                next.caption = Caption(agent: agent, text: text, utteranceId: utteranceId)
                next.errorText = nil
            case .agentState(let agent, let state):
                next.presence[agent] = state
                // The server never sends idle or listening for the agent that just finished, so when one agent
                // starts working the other one is no longer the active speaker.
                if state == .thinking || state == .speaking {
                    let other: Agent = agent == .helper ? .shopper : .helper
                    next.presence[other] = .idle
                }
                next.wait = waitAfter(agent: agent, presence: state, nowMs: nowMs)
            case .turn(let id, let stage, _, _) where stage.isExpertSignal:
                if stage == .expert {
                    next.expertTurnId = id
                } else if expertTurnId == id {
                    next.expertTurnId = nil
                }
            case .turn(let id, let stage, let typicalMs, let detail):
                next.wait = waitAfter(turnId: id, stage: stage, typicalMs: typicalMs, nowMs: nowMs)
                if stage == .failed, let detail, !detail.isEmpty {
                    next.errorText = detail
                }
            case .marketResults(let response):
                next.market = response
            case .error(_, let errorMessage):
                next.errorText = errorMessage
                next.wait = nil
            case .audio, .ignored:
                break
            }
        }
        return next
    }

    /// A `turn` message: a new turn id restarts the clock, a later stage of the same turn keeps it.
    private func waitAfter(turnId: String, stage: TurnStage, typicalMs: Int?, nowMs: Double) -> Wait? {
        guard stage.isWaiting else { return nil }
        let isSameTurn = wait?.turnId == turnId
        return Wait(
            turnId: turnId,
            agent: isSameTurn ? (wait?.agent ?? .helper) : .helper,
            stage: stage,
            startedAtMs: isSameTurn ? (wait?.startedAtMs ?? nowMs) : nowMs,
            typicalMs: typicalMs ?? (isSameTurn ? wait?.typicalMs : nil)
        )
    }

    /// `agent_state`: thinking starts a wait if none is running (the shopper sends no `turn`), speaking ends it.
    private func waitAfter(agent: Agent, presence: PresenceState, nowMs: Double) -> Wait? {
        switch presence {
        case .thinking:
            if let wait, wait.agent == agent { return wait }
            return Wait(turnId: nil, agent: agent, stage: .thinking, startedAtMs: nowMs, typicalMs: nil)
        case .speaking:
            return nil
        case .idle, .listening:
            return wait
        }
    }

    /// The wait to show at `nowMs`, or nil once it is older than `Tuning.maxWaitMs` (the server never ended it).
    public func visibleWait(nowMs: Double) -> Wait? {
        guard let wait, nowMs - wait.startedAtMs <= Tuning.maxWaitMs else { return nil }
        return wait
    }

    /// The "Power is off" button shows for modes that need it, until it has been tapped on this connection.
    public func showsPowerOffButton(for mode: Mode) -> Bool {
        mode.requiresPowerOff && !powerOffConfirmed
    }
}
