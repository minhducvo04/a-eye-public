import Foundation

/// Consent is evaluated for every connection; URL settings cannot bypass the mode picker.
public enum LiveConfiguration {
    public static func consent(_ saved: Bool?) -> Bool { saved ?? true }

    public static func enabled(mode: String, google: Bool, privateMode: Bool) -> Bool {
        ["gemini-live", "general"].contains(mode) && google && !privateMode
    }

    public static func url(_ base: URL, live: Bool, google: Bool, privateMode: Bool, engineering: Bool = false) -> URL? {
        guard !live || (google && !privateMode),
              var parts = URLComponents(url: base, resolvingAgainstBaseURL: false) else { return nil }
        var items = (parts.queryItems ?? []).filter { $0.name != "mode" && $0.name != "google" }
        if engineering {
            items.append(URLQueryItem(name: "mode", value: "engineering"))
        } else if live {
            items += [URLQueryItem(name: "mode", value: "gemini-live"), URLQueryItem(name: "google", value: "1")]
        }
        parts.queryItems = items.isEmpty ? nil : items
        return parts.url
    }

    public static func transcript(_ data: Data) -> String? {
        guard let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              object["type"] as? String == "turn" else { return nil }
        return object["text"] as? String
    }
}

public struct ServerAudio: Decodable, Equatable {
    public let agent: Agent
    public let utteranceId: String
    public let pcm16B64: String
    public let sampleRate: Int?
    private let finalValue: Bool?
    public var final: Bool { finalValue ?? false }
    public var pcm: Data { Data(base64Encoded: pcm16B64) ?? Data() }
    public var isValid: Bool {
        (sampleRate == nil || sampleRate == 16000) && Data(base64Encoded: pcm16B64) != nil && pcm.count % 2 == 0
    }
    enum CodingKeys: String, CodingKey {
        case agent, utteranceId, pcm16B64, sampleRate
        case finalValue = "final"
    }
}

extension ClientMessage {
    public static func audioIn(sessionId: String, seq: Int, pcm: Data, final: Bool) -> Data {
        wire(["v": 2, "type": "audio_in", "session_id": sessionId, "seq": seq,
              "sample_rate": 16000, "pcm16_b64": pcm.base64EncodedString(), "final": final])
    }

    public static func camera(sessionId: String, on: Bool) -> Data {
        wire(["v": 2, "type": "camera", "session_id": sessionId, "on": on])
    }

    private static func wire(_ object: [String: Any]) -> Data {
        (try? JSONSerialization.data(withJSONObject: object)) ?? Data()
    }
}

/// Marks utterance ends for the server's normal-pipeline fallback as well as Live.
public struct LiveAudioGate {
    private var speaking = false
    public var isActive: Bool { speaking }
    private var silence = 0.0
    private var elapsed = 0.0
    public init() {}
    public mutating func consume(rms: Float, duration: Double) -> Bool {
        if rms > 0.015 { speaking = true; silence = 0 }
        else if speaking { silence += duration }
        if speaking { elapsed += duration }
        if speaking && (silence >= 0.7 || elapsed >= 15) {
            self = LiveAudioGate()
            return true
        }
        return false
    }
}
