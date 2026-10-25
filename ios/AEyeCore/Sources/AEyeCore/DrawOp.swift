import Foundation

public enum Agent: String, Codable {
    case helper, shopper
}

public enum PresenceState: String, Codable {
    case idle, listening, thinking, speaking
}

public enum DrawRole: String, Codable {
    case target, info, warn, ok

    /// Role colour as `#RRGGBB`.
    public var hex: String {
        switch self {
        case .target: return "#3DDC97"
        case .info: return "#9EB6FF"
        case .warn: return "#FF8A3D"
        case .ok: return "#4C9AFF"
        }
    }
}

/// One drawing command. Coordinates are normalised 0..1 of the frame that was sent.
/// Decoded with `.convertFromSnakeCase`; keys the app does not use (e.g. `anchor`) are ignored.
public struct DrawOp: Decodable, Equatable {
    public let kind: String
    public let id: String
    public let role: DrawRole?
    public let label: String?
    public let ttlMs: Int?
    public let bbox: [Double]?
    public let center: [Double]?
    public let r: Double?
    public let from: [Double]?
    public let to: [Double]?
    public let points: [[Double]]?
    public let at: [Double]?
    public let text: String?

    public init(kind: String, id: String, role: DrawRole?, label: String?, ttlMs: Int?, bbox: [Double]?,
                center: [Double]?, r: Double?, from: [Double]?, to: [Double]?, points: [[Double]]?,
                at: [Double]?, text: String?) {
        self.kind = kind
        self.id = id
        self.role = role
        self.label = label
        self.ttlMs = ttlMs
        self.bbox = bbox
        self.center = center
        self.r = r
        self.from = from
        self.to = to
        self.points = points
        self.at = at
        self.text = text
    }

    private enum CodingKeys: String, CodingKey {
        case kind, id, role, label, ttlMs, bbox, center, r, from, to, points, at, text
    }

    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        kind = try c.decode(String.self, forKey: .kind)
        id = try c.decode(String.self, forKey: .id)
        // An unknown role must not drop the whole draw message; the renderer treats nil as info.
        role = try? c.decodeIfPresent(DrawRole.self, forKey: .role)
        label = try c.decodeIfPresent(String.self, forKey: .label)
        ttlMs = try c.decodeIfPresent(Int.self, forKey: .ttlMs)
        bbox = try c.decodeIfPresent([Double].self, forKey: .bbox)
        center = try c.decodeIfPresent([Double].self, forKey: .center)
        r = try c.decodeIfPresent(Double.self, forKey: .r)
        from = try c.decodeIfPresent([Double].self, forKey: .from)
        to = try c.decodeIfPresent([Double].self, forKey: .to)
        points = try c.decodeIfPresent([[Double]].self, forKey: .points)
        at = try c.decodeIfPresent([Double].self, forKey: .at)
        text = try c.decodeIfPresent(String.self, forKey: .text)
    }
}
