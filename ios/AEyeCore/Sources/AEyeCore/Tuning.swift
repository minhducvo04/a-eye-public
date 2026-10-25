import Foundation

/// Constants shared by the app. Values come from the plan's Global Constraints.
public enum Tuning {
    public static let frameInterval: TimeInterval = 1.0
    public static let maxFrameSide = 640
    public static let jpegQuality = 0.7
    public static let reconnectDelay: TimeInterval = 2.0
    public static let demoBoardId = "demo-b650"
    /// Expected wait for an answer before the server has measured any turns (`typical_ms`).
    public static let defaultTypicalWaitMs: Double = 3000
    /// The model always sees the whole frame; the sweep animation is off so the screen does not suggest otherwise.
    public static let showScanSweep = false
    /// A wait card older than this is hidden, in case the server never sends the turn's end.
    public static let maxWaitMs: Double = 30000
}

/// Scales (width, height) so the long side is at most `maxSide`, keeping aspect. Never upscales.
public func frameSize(width: Int, height: Int, maxSide: Int = Tuning.maxFrameSide) -> (width: Int, height: Int) {
    let longSide = max(width, height)
    guard width > 0, height > 0, maxSide > 0, longSide > maxSide else {
        return (width, height)
    }
    let scale = Double(maxSide) / Double(longSide)
    let scaledWidth = max(1, Int((Double(width) * scale).rounded()))
    let scaledHeight = max(1, Int((Double(height) * scale).rounded()))
    return (scaledWidth, scaledHeight)
}

/// Turns what the user typed in Settings into the `/ws2` socket URL, or nil when it is unusable.
/// - no scheme -> `ws://`; `http` -> `ws`; `https` -> `wss`; empty or `/` path -> `/ws2`.
/// - a non-empty `token` is sent as the `token` query item, the same way `agent.html?token=` does on the web.
public func serverURL(from raw: String, token: String = "") -> URL? {
    guard let base = baseServerURL(from: raw) else { return nil }
    let secret = token.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !secret.isEmpty, var components = URLComponents(url: base, resolvingAgainstBaseURL: false) else {
        return base
    }
    let others = (components.queryItems ?? []).filter { $0.name != "token" }
    components.queryItems = others + [URLQueryItem(name: "token", value: secret)]
    return components.url
}

private func baseServerURL(from raw: String) -> URL? {
    let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
    guard !trimmed.isEmpty else { return nil }

    let withScheme = trimmed.contains("://") ? trimmed : "ws://" + trimmed
    guard var components = URLComponents(string: withScheme),
          let scheme = components.scheme?.lowercased(),
          let host = components.host, !host.isEmpty
    else { return nil }

    switch scheme {
    case "ws", "wss": components.scheme = scheme
    case "http": components.scheme = "ws"
    case "https": components.scheme = "wss"
    default: return nil
    }

    if components.path.isEmpty || components.path == "/" {
        components.path = "/ws2"
    }
    return components.url
}
