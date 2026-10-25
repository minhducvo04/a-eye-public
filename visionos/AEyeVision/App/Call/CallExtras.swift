import AEyeCore
import Foundation

// Wire pieces the iPhone's `AEyeCore` does not have yet (v2.4 to v2.7 in `contracts/CHANGELOG.md`), kept in
// `visionos/` so `ios/` stays untouched: the full `hello`, `rewind`, the expert turn stages, Rewind state, hits
// and recaps, and `track` boxes. Ports of the web client's `rewind.js`, `turn.js` and `memory.js` helpers.

enum Wire {
    /// `hello` with every option the headset uses. Optional fields are left out, never null
    /// (`additionalProperties: false`).
    static func hello(sessionId: String, pack: String, serverVoice: Bool, voice: String?, rewind: Bool,
                      isPrivate: Bool) -> Data {
        var body: [String: Any] = [
            "v": 2, "type": "hello", "session_id": sessionId, "pack": pack, "client": "ios",
            "tts": serverVoice ? "server" : "browser",
        ]
        if pack == "pc_build" { body["board_id"] = Tuning.demoBoardId }
        if serverVoice, let voice, !voice.isEmpty { body["voice"] = voice }
        if rewind { body["rewind"] = true }
        if isPrivate { body["private"] = true }
        return (try? JSONSerialization.data(withJSONObject: body)) ?? Data()
    }

    static func rewind(sessionId: String, on: Bool, keep: Bool) -> Data {
        let body: [String: Any] = ["v": 2, "type": "rewind", "session_id": sessionId, "on": on, "keep": keep]
        return (try? JSONSerialization.data(withJSONObject: body)) ?? Data()
    }
}

struct SceneBox: Decodable, Equatable {
    let id: String?
    let label: String
    let bbox: [Double]
    let conf: Double?
}

struct RewindHit: Decodable, Equatable, Identifiable {
    let turnId: String?
    let query: String?
    let tS: Double
    let sure: Bool
    let alternatives: [Double]?
    let frameB64: String?
    let objects: [SceneBox]?

    var id: String { "\(turnId ?? "")-\(tS)" }

    enum CodingKeys: String, CodingKey {
        case query, sure, alternatives, objects
        case turnId = "turn_id"
        case tS = "t_s"
        case frameB64 = "frame_b64"
    }
}

struct RecapStep: Decodable, Equatable {
    let tS: Double
    let text: String

    enum CodingKeys: String, CodingKey {
        case text
        case tS = "t_s"
    }
}

/// The Recap sheet: waiting, steps, or a single line (nothing to recap, or unavailable).
struct Recap: Equatable, Identifiable {
    var pending = false
    var steps: [RecapStep] = []
    var skipped: [String] = []
    var say: String?
    let id = UUID()
}

struct RewindStatus: Equatable {
    var on: Bool
    var clips: Int
    var locked: Bool
}

enum ExtraMessage {
    case expert(turnId: String, detail: String?)
    case expertDone(turnId: String)
    case rewindState(RewindStatus)
    case rewindHit(RewindHit)
    case rewindRecap(Recap)
    case track([SceneBox])

    /// nil for everything `AEyeCore.ServerMessage` handles (including `turn` stages other than the expert ones).
    static func decode(_ data: Data) -> ExtraMessage? {
        guard let probe = try? JSONDecoder().decode(Probe.self, from: data) else { return nil }
        switch probe.type {
        case "turn":
            guard let id = probe.turnId else { return nil }
            if probe.stage == "expert" { return .expert(turnId: id, detail: probe.detail) }
            if probe.stage == "expert_done" { return .expertDone(turnId: id) }
            return nil
        case "rewind_state":
            return .rewindState(RewindStatus(on: probe.on ?? false, clips: probe.clips ?? 0, locked: probe.locked ?? false))
        case "rewind_hit":
            return (try? JSONDecoder().decode(RewindHit.self, from: data)).map(ExtraMessage.rewindHit)
        case "rewind_recap":
            guard let p = try? JSONDecoder().decode(RecapPayload.self, from: data) else { return nil }
            return .rewindRecap(Recap(steps: p.steps ?? [], skipped: p.skipped ?? [], say: p.say))
        case "track":
            guard let p = try? JSONDecoder().decode(TrackPayload.self, from: data) else { return nil }
            return .track(p.objects)
        default:
            return nil
        }
    }

    private struct Probe: Decodable {
        let type: String
        let turnId: String?
        let stage: String?
        let detail: String?
        let on: Bool?
        let clips: Int?
        let locked: Bool?

        enum CodingKeys: String, CodingKey {
            case type, stage, detail, on, clips, locked
            case turnId = "turn_id"
        }
    }

    struct RecapPayload: Decodable {
        let steps: [RecapStep]?
        let skipped: [String]?
        let say: String?
    }

    private struct TrackPayload: Decodable {
        let objects: [SceneBox]
    }
}

/// Words the headset shows, the same as the web page's.
enum Copy {
    /// `client-web/rewind.js` NOTICE.
    static let rewindNotice = "When on, short video clips of this call are uploaded to TwelveLabs so you can ask about them later."
    static let rewindFoot = "They are deleted from TwelveLabs when the call ends unless you keep them."
    static let privateFoot = "Nothing leaves our own machines: the local brain answers, Kokoro speaks, speech is recognized on this Vision Pro, and Rewind stays off."

    /// `fmtCallTime`: seconds since the call started as m:ss.
    static func callTime(_ seconds: Double) -> String {
        let s = max(0, Int(seconds.rounded()))
        return String(format: "%d:%02d", s / 60, s % 60)
    }

    /// `hitCaption`.
    static func hitCaption(_ hit: RewindHit) -> String {
        let when = "\(callTime(hit.tS)) into the call"
        if hit.sure { return when }
        let others = (hit.alternatives ?? []).map(callTime)
        return "Best guess: \(when)" + (others.isEmpty ? "" : ", or \(others.joined(separator: ", "))")
    }

    /// `memory.js` countLine.
    static func countLine(_ n: Int) -> String {
        if n <= 0 { return "Nothing remembered today" }
        return "Remembering \(n) \(n == 1 ? "thing" : "things") today"
    }

    /// `memory.js` agoLine, without the caregiver's place (that settings store has not landed).
    static func agoLine(hitS: Double, callNowS: Double) -> String {
        let ago = max(0, Int((callNowS - hitS).rounded()))
        return ago < 5 ? "Seen just now" : "Seen \(callTime(Double(ago))) ago"
    }

    /// `memory.js` matchLive: the live box sharing a word with a remembered label, most confident first.
    static func matchLive(_ remembered: [SceneBox], _ live: [SceneBox]) -> SceneBox? {
        let wanted = Set(remembered.flatMap { words($0.label) })
        guard !wanted.isEmpty else { return nil }
        return live.filter { words($0.label).contains(where: wanted.contains) }
            .max { ($0.conf ?? 0) < ($1.conf ?? 0) }
    }

    private static func words(_ text: String) -> [String] {
        text.lowercased().split { !($0.isLetter || $0.isNumber) }.map(String.init)
    }
}

/// `/api/*` on the same host as the socket, with the socket URL's `?token=` as a bearer header when present.
/// Bodies hold only the session id; nothing is logged.
enum ServerAPI {
    static func request(_ path: String, socketURL: URL, body: [String: Any]? = nil) async -> (Int, Data)? {
        guard var components = URLComponents(url: socketURL, resolvingAgainstBaseURL: false) else { return nil }
        let token = components.queryItems?.first { $0.name == "token" }?.value
        components.scheme = components.scheme == "wss" ? "https" : "http"
        let parts = path.split(separator: "?", maxSplits: 1).map(String.init)
        components.path = parts[0]
        components.query = parts.count > 1 ? parts[1] : nil
        guard let url = components.url else { return nil }
        var request = URLRequest(url: url)
        request.timeoutInterval = 20
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try? JSONSerialization.data(withJSONObject: body)
        }
        guard let (data, response) = try? await URLSession(configuration: .ephemeral).data(for: request),
              let http = response as? HTTPURLResponse else { return nil }
        return (http.statusCode, data)
    }

    /// Remembered count: `/api/memory/status` when the memory server has landed, else this call's Rewind clips
    /// (the same fallback as `client-web/memory.js`).
    static func memoryCount(sessionId: String, socketURL: URL) async -> Int? {
        let q = "?session_id=\(sessionId)"
        if let (code, data) = await request("/api/memory/status" + q, socketURL: socketURL), !isMissing(code) {
            return code == 200 ? jsonInt(data, "count") : nil
        }
        guard let (code, data) = await request("/api/rewind/status" + q, socketURL: socketURL), code == 200 else { return nil }
        return jsonInt(data, "clips")
    }

    /// Forget: `/api/memory/forget` with `scope` (last or today) when it exists, else this call's Rewind clips.
    /// Returns the number of clips the server dropped when it says, or nil on failure.
    static func forget(sessionId: String, scope: String?, socketURL: URL) async -> Int? {
        if let scope, let (code, data) = await request("/api/memory/forget", socketURL: socketURL,
                                                    body: ["session_id": sessionId, "scope": scope]), !isMissing(code) {
            return code == 200 ? (jsonInt(data, "count") ?? 0) : nil
        }
        guard let (code, data) = await request("/api/rewind/forget", socketURL: socketURL,
                                               body: ["session_id": sessionId]), code == 200 else { return nil }
        return jsonInt(data, "deleted") ?? 0
    }

    static func recap(sessionId: String, socketURL: URL) async -> Recap {
        guard let (code, data) = await request("/api/rewind/recap", socketURL: socketURL, body: ["session_id": sessionId]),
              code == 200, let p = try? JSONDecoder().decode(ExtraMessage.RecapPayload.self, from: data) else {
            return Recap(say: "The recap isn't available right now.")
        }
        return Recap(steps: p.steps ?? [], skipped: p.skipped ?? [], say: p.say)
    }

    /// The memory routes have not landed on this server: 404, or 405 from the static-file route for a POST.
    private static func isMissing(_ code: Int) -> Bool {
        code == 404 || code == 405
    }

    private static func jsonInt(_ data: Data, _ key: String) -> Int? {
        (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?[key] as? Int
    }
}
