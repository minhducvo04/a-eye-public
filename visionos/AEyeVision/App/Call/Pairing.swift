import Foundation

/// App-local additions to the v2 contract; unknown JSON fields are ignored by Decodable.
struct PairingMessage: Decodable {
    let v: Int
    let type: String
    var code: String?
    var expires_in_s: Int?
    var path: String?
    var state: String?
    var camera_on: Bool?
    var source: String?
    var role: String?
    var detail: String?

    static func decode(_ data: Data) -> Self? {
        guard let message = try? JSONDecoder().decode(Self.self, from: data), message.v == 2,
              message.type == "pair" || message.type == "pair_status" else { return nil }
        return message
    }

    static func request(sessionId: String) -> Data {
        try! JSONSerialization.data(withJSONObject: ["v": 2, "type": "pair_request", "session_id": sessionId])
    }

    func link(socketURL: URL) -> URL? {
        guard let code, var base = URLComponents(url: socketURL, resolvingAgainstBaseURL: false),
              let relative = URLComponents(string: path ?? "/agent.html?pair=\(code)"),
              relative.scheme == nil, relative.host == nil else { return nil }
        let token = base.queryItems?.first { $0.name == "token" }
        base.scheme = base.scheme == "wss" ? "https" : "http"
        base.path = relative.path.hasPrefix("/") ? relative.path : "/" + relative.path
        base.queryItems = relative.queryItems
        base.fragment = nil
        if let token {
            base.queryItems = (base.queryItems ?? []).filter { $0.name != "token" } + [token]
        }
        return base.url
    }
}

struct PairingState {
    var offer: PairingMessage?
    var expiresAt: Date?
    var joined = false
    var cameraOn = false
    var status = ""

    mutating func receive(_ message: PairingMessage, now: Date = .now) {
        if message.type == "pair" {
            guard !joined, let code = message.code, (4...8).contains(code.count),
                  code.allSatisfy({ $0 >= "0" && $0 <= "9" }),
                  let seconds = message.expires_in_s, seconds > 0 else { return }
            offer = message
            expiresAt = now.addingTimeInterval(Double(seconds))
            status = "Scan with your phone or enter the code."
            return
        }
        switch message.state {
        case "joined":
            joined = true
            cameraOn = message.camera_on ?? true
            offer = nil
            expiresAt = nil
            status = cameraOn ? "Phone camera connected" : "Phone camera off"
        case "camera":
            guard joined || message.source == "camera" else { return }
            joined = true
            cameraOn = message.camera_on ?? cameraOn
            offer = nil
            expiresAt = nil
            status = cameraOn ? "Phone camera connected" : "Phone camera off"
        case "left":
            self = Self()
            status = "Phone camera left"
        case "rejected", "expired":
            offer = nil
            expiresAt = nil
            status = message.state == "expired" ? "Pairing code expired" : "Pairing rejected"
        default: break
        }
    }
}
