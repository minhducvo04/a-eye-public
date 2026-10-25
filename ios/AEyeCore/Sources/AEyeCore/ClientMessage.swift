import Foundation

/// Encoders for every message the app sends on `/ws2`. All output is UTF-8 JSON with `"v": 2`.
/// Optional fields are omitted (never `null`) because the schema sets `additionalProperties: false`.
public enum ClientMessage {
    private static let version = 2

    public static func hello(sessionId: String, pack: String, serverAudio: Bool = false, privateMode: Bool = false) -> Data {
        encode(HelloPayload(
            v: version,
            type: "hello",
            sessionId: sessionId,
            pack: pack,
            boardId: pack == "pc_build" ? Tuning.demoBoardId : nil,
            client: "ios",
            tts: serverAudio ? "server" : "browser",
            stt: serverAudio ? "server" : nil,
            privateMode: privateMode ? true : nil
        ))
    }

    public static func frame(sessionId: String, seq: Int, tsMs: Int64, width: Int, height: Int, jpeg: Data) -> Data {
        encode(FramePayload(
            v: version,
            type: "frame",
            sessionId: sessionId,
            seq: seq,
            tsMs: tsMs,
            w: width,
            h: height,
            jpegB64: jpeg.base64EncodedString()
        ))
    }

    /// Always a final transcript or typed line; `source` is left out.
    public static func userText(sessionId: String, text: String) -> Data {
        encode(UserTextPayload(v: version, type: "user_text", sessionId: sessionId, text: text, isFinal: true))
    }

    public static func bargeIn(sessionId: String, utteranceId: String, atMs: Int) -> Data {
        encode(BargeInPayload(v: version, type: "barge_in", sessionId: sessionId, utteranceId: utteranceId, atMs: atMs))
    }

    public static func powerOff(sessionId: String) -> Data {
        encode(ConfirmPayload(v: version, type: "confirm", sessionId: sessionId, what: "power_off", value: true))
    }

    private static func encode<T: Encodable>(_ payload: T) -> Data {
        (try? JSONEncoder().encode(payload)) ?? Data()
    }
}

private struct HelloPayload: Encodable {
    let v: Int
    let type: String
    let sessionId: String
    let pack: String
    let boardId: String?
    let client: String
    let tts: String
    let stt: String?
    let privateMode: Bool?

    enum CodingKeys: String, CodingKey {
        case v, type, pack, client, tts, stt
        case privateMode = "private"
        case sessionId = "session_id"
        case boardId = "board_id"
    }
}

private struct FramePayload: Encodable {
    let v: Int
    let type: String
    let sessionId: String
    let seq: Int
    let tsMs: Int64
    let w: Int
    let h: Int
    let jpegB64: String

    enum CodingKeys: String, CodingKey {
        case v, type, seq, w, h
        case sessionId = "session_id"
        case tsMs = "ts_ms"
        case jpegB64 = "jpeg_b64"
    }
}

private struct UserTextPayload: Encodable {
    let v: Int
    let type: String
    let sessionId: String
    let text: String
    let isFinal: Bool

    enum CodingKeys: String, CodingKey {
        case v, type, text
        case sessionId = "session_id"
        case isFinal = "final"
    }
}

private struct BargeInPayload: Encodable {
    let v: Int
    let type: String
    let sessionId: String
    let utteranceId: String
    let atMs: Int

    enum CodingKeys: String, CodingKey {
        case v, type
        case sessionId = "session_id"
        case utteranceId = "utterance_id"
        case atMs = "at_ms"
    }
}

private struct ConfirmPayload: Encodable {
    let v: Int
    let type: String
    let sessionId: String
    let what: String
    let value: Bool

    enum CodingKeys: String, CodingKey {
        case v, type, what, value
        case sessionId = "session_id"
    }
}
