import Foundation

/// A message from the server on `/ws2`, reduced to what the app acts on.
/// Types the app does not use (`metrics`, `track`, ...) and known types that fail to decode
/// become `.ignored`, so state never changes on them.
public enum ServerMessage: Equatable {
    case audio(ServerAudio)
    case draw(agent: Agent, seq: Int, ops: [DrawOp])
    case say(agent: Agent, utteranceId: String, text: String)
    case agentState(agent: Agent, state: PresenceState)
    case turn(id: String, stage: TurnStage, typicalMs: Int?, detail: String?)
    case marketResults(MarketResponse)
    case error(code: String, message: String)
    case ignored(type: String)

    /// Returns nil when `data` is not a JSON object with a string `type`.
    public static func decode(_ data: Data) -> ServerMessage? {
        guard let probe = try? JSONDecoder().decode(TypeProbe.self, from: data) else { return nil }

        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        let ignored = ServerMessage.ignored(type: probe.type)

        switch probe.type {
        case "audio":
            guard let audio = try? decoder.decode(ServerAudio.self, from: data),
                  audio.isValid else { return ignored }
            return .audio(audio)
        case "draw":
            guard let p = try? decoder.decode(DrawPayload.self, from: data) else { return ignored }
            return .draw(agent: p.agent, seq: p.seq, ops: p.ops)
        case "say":
            guard let p = try? decoder.decode(SayPayload.self, from: data) else { return ignored }
            return .say(agent: p.agent, utteranceId: p.utteranceId, text: p.text)
        case "agent_state":
            guard let p = try? decoder.decode(AgentStatePayload.self, from: data) else { return ignored }
            return .agentState(agent: p.agent, state: p.state)
        case "turn":
            guard let p = try? decoder.decode(TurnPayload.self, from: data) else { return ignored }
            return .turn(id: p.turnId, stage: p.stage, typicalMs: p.typicalMs, detail: p.detail)
        case "market_results":
            guard let p = try? decoder.decode(MarketResultsPayload.self, from: data) else { return ignored }
            return .marketResults(p.result)
        case "error":
            guard let p = try? decoder.decode(ErrorPayload.self, from: data) else { return ignored }
            return .error(code: p.code, message: p.message)
        default:
            return ignored
        }
    }
}

private struct TypeProbe: Decodable {
    let type: String
}

private struct DrawPayload: Decodable {
    let agent: Agent
    let seq: Int
    let ops: [DrawOp]
}

private struct SayPayload: Decodable {
    let agent: Agent
    let utteranceId: String
    let text: String
}

private struct AgentStatePayload: Decodable {
    let agent: Agent
    let state: PresenceState
}

private struct TurnPayload: Decodable {
    let turnId: String
    let stage: TurnStage
    let typicalMs: Int?
    let detail: String?
}

private struct MarketResultsPayload: Decodable {
    let result: MarketResponse
}

private struct ErrorPayload: Decodable {
    let code: String
    let message: String
}
