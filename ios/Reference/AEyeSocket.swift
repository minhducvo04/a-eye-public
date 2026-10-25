import Foundation
#if canImport(UIKit)
import UIKit
#endif

struct Anchor: Codable { let object_id: String; let bbox: [Double] }
struct OpFields: Codable {
    let id: String
    var role: String?; var label: String?; var ttl_ms: Int?; var anchor: Anchor?
    var bbox: [Double]?; var center: [Double]?; var r: Double?
    var from: [Double]?; var to: [Double]?; var points: [[Double]]?
    var at: [Double]?; var text: String?
}
// Associated values preserve geometry and common fields, including tracking anchors.
enum DrawOp: Codable {
    case box(OpFields), highlight(OpFields), circle(OpFields), arrow(OpFields)
    case path(OpFields), label(OpFields), pin(OpFields), clear(OpFields), unknown
    private enum Keys: String, CodingKey { case kind }
    init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: Keys.self)
        let kind = try c.decode(String.self, forKey: .kind)
        guard ["box", "highlight", "circle", "arrow", "path", "label", "pin", "clear"].contains(kind)
        else { self = .unknown; return }
        let f = try OpFields(from: decoder)
        switch kind {
        case "box": self = .box(f)
        case "highlight": self = .highlight(f)
        case "circle": self = .circle(f)
        case "arrow": self = .arrow(f)
        case "path": self = .path(f)
        case "label": self = .label(f)
        case "pin": self = .pin(f)
        default: self = .clear(f)
        }
    }
    func encode(to encoder: Encoder) throws {
        var c = encoder.container(keyedBy: Keys.self)
        let kind: String; let f: OpFields
        switch self {
        case .box(let v): kind = "box"; f = v
        case .highlight(let v): kind = "highlight"; f = v
        case .circle(let v): kind = "circle"; f = v
        case .arrow(let v): kind = "arrow"; f = v
        case .path(let v): kind = "path"; f = v
        case .label(let v): kind = "label"; f = v
        case .pin(let v): kind = "pin"; f = v
        case .clear(let v): kind = "clear"; f = v
        case .unknown: try c.encode("unknown", forKey: .kind); return
        }
        try c.encode(kind, forKey: .kind)
        try f.encode(to: encoder)
    }
}
struct Say: Codable {
    let agent: String; let utterance_id: String; let text: String
    var turn_id: String?; var part: Int?; var final: Bool?
}
struct Draw: Codable { let agent: String; let seq: Int; let ops: [DrawOp] }
struct AgentState: Codable { let agent: String; let state: String; var focus: [Double]? }
struct Audio: Codable {
    let agent: String; let utterance_id: String; let pcm16_b64: String
    var sample_rate: Int?; var final: Bool?
    var pcm: Data? { Data(base64Encoded: pcm16_b64) }
}
struct Metrics: Codable {
    var turn_id: String?; var seq: Int?; var vlm_ms: Double?
    var brain_first_token_ms: Double?; var first_audio_ms: Double?
    var turn_ms: Double?; var track_ms: Double?
}
struct SceneObject: Codable { let id: String; let label: String; let bbox: [Double]; let conf: Double }
struct Track: Codable { let seq: Int; let objects: [SceneObject] }
struct WireError: Codable { let code: String; let message: String }
enum Event {
    case say(Say), draw(Draw), agentState(AgentState), audio(Audio)
    case metrics(Metrics), track(Track), error(WireError)
    static func decode(_ data: Data) throws -> Event? {
        struct Header: Decodable { let v: Int; let type: String }
        let d = JSONDecoder()
        let h = try d.decode(Header.self, from: data)
        guard h.v == 2 else { return nil }
        switch h.type {
        case "say": return .say(try d.decode(Say.self, from: data))
        case "draw": return .draw(try d.decode(Draw.self, from: data))
        case "agent_state": return .agentState(try d.decode(AgentState.self, from: data))
        case "audio": return .audio(try d.decode(Audio.self, from: data))
        case "metrics": return .metrics(try d.decode(Metrics.self, from: data))
        case "track": return .track(try d.decode(Track.self, from: data))
        case "error": return .error(try d.decode(WireError.self, from: data))
        default: return nil // Includes market_results; add a shop UI separately.
        }
    }
}

@MainActor
final class AEyeSocket {
    var onEvent: ((Event) -> Void)?
    var onFailure: ((Error) -> Void)?
    var onReset: (() -> Void)? // Clear overlays, audio and confirmation before every hello.
    var onUnauthorized: (() -> Void)?
    private let url: URL
    private let token: String?
    private let pack: String
    private let boardID: String?
    private let tts: String
    private let stt: String
    private var socket: URLSessionWebSocketTask?
    private var runner: Task<Void, Never>?
    private var sessionID = ""
    private var sequence = 0
    private var ready = false
    private var sendingFrame = false
    init(url: URL, token: String? = nil, pack: String = "general", boardID: String? = nil,
         tts: String = "server", stt: String = "server") {
        self.url = url; self.token = token; self.pack = pack; self.boardID = boardID
        self.tts = tts; self.stt = stt
    }
    func connect() {
        disconnect()
        runner = Task { [weak self] in await self?.run() }
    }
    func disconnect() {
        runner?.cancel(); runner = nil; ready = false
        socket?.cancel(with: .normalClosure, reason: nil); socket = nil
    }
    private func run() async {
        var delay = 1.0
        while !Task.isCancelled {
            var request = URLRequest(url: url)
            if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
            let ws = URLSession.shared.webSocketTask(with: request)
            socket = ws; sessionID = UUID().uuidString; sequence = 0
            onReset?(); ws.resume()
            do {
                var hello: [String: Any] = ["pack": pack, "client": "ios", "tts": tts, "stt": stt]
                if let boardID { hello["board_id"] = boardID }
                try await send("hello", hello)
                while !Task.isCancelled {
                    let message = try await ws.receive()
                    guard socket === ws else { return }
                    let data: Data
                    switch message {
                    case .string(let s): data = Data(s.utf8)
                    case .data(let d): data = d
                    @unknown default: continue
                    }
                    do {
                        if let event = try Event.decode(data) {
                            if case .agentState(let state) = event, state.state == "listening" {
                                ready = true; delay = 1
                            }
                            onEvent?(event)
                        }
                    } catch { onFailure?(error) }
                }
            } catch {
                guard !Task.isCancelled, socket === ws else { return }
                ready = false
                if ws.closeCode.rawValue == 4401 { onUnauthorized?(); return }
                onFailure?(error)
            }
            ws.cancel(with: .goingAway, reason: nil)
            do { try await Task.sleep(nanoseconds: UInt64((delay + Double.random(in: 0...0.25)) * 1e9)) }
            catch { return }
            delay = min(delay * 2, 30)
        }
    }
    private func send(_ type: String, _ fields: [String: Any]) async throws {
        guard let socket else { throw URLError(.notConnectedToInternet) }
        var body = fields
        body["v"] = 2; body["type"] = type; body["session_id"] = sessionID
        let data = try JSONSerialization.data(withJSONObject: body)
        try await socket.send(.string(String(decoding: data, as: UTF8.self)))
    }
    private func requireReady() throws {
        if !ready { throw URLError(.notConnectedToInternet) }
    }
    func userText(_ text: String, final: Bool = true) async throws {
        try requireReady()
        try await send("user_text", ["text": text, "final": final, "source": "typed"])
    }
    func audioIn(_ pcm16: Data, final: Bool) async throws {
        try requireReady()
        guard pcm16.count % 2 == 0, pcm16.count <= 1_920_000 else { throw URLError(.dataLengthExceedsMaximum) }
        sequence += 1
        try await send("audio_in", ["seq": sequence, "sample_rate": 16000,
                                   "pcm16_b64": pcm16.base64EncodedString(), "final": final])
    }
    func bargeIn(utteranceID: String, atMS: Int) async throws {
        try requireReady()
        try await send("barge_in", ["utterance_id": utteranceID, "at_ms": atMS])
    }
    func confirm(_ what: String, value: Bool) async throws {
        try requireReady()
        try await send("confirm", ["what": what, "value": value])
    }
    #if canImport(UIKit)
    // Invoke at about 1 fps. Busy/disconnected frames are dropped, never queued.
    func frame(_ cgImage: CGImage) async throws { try await frame(UIImage(cgImage: cgImage)) }
    func frame(_ image: UIImage) async throws {
        guard ready, !sendingFrame, image.size.width > 0, image.size.height > 0 else { return }
        sendingFrame = true
        defer { sendingFrame = false }
        let scale = min(1, 640 / max(image.size.width, image.size.height))
        let size = CGSize(width: max(1, floor(image.size.width * scale)),
                          height: max(1, floor(image.size.height * scale)))
        let format = UIGraphicsImageRendererFormat(); format.scale = 1; format.opaque = true
        let resized = UIGraphicsImageRenderer(size: size, format: format).image { _ in
            image.draw(in: CGRect(origin: .zero, size: size))
        }
        guard let jpeg = resized.jpegData(compressionQuality: 0.7) else { throw URLError(.cannotEncodeContentData) }
        sequence += 1
        try await send("frame", ["seq": sequence, "ts_ms": Int(Date().timeIntervalSince1970 * 1000),
                                 "w": Int(size.width), "h": Int(size.height), "jpeg_b64": jpeg.base64EncodedString()])
    }
    #endif
}
