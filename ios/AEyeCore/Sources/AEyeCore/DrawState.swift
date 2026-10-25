import Foundation

public struct DrawItem: Equatable {
    public let op: DrawOp
    public let agent: Agent
    public let bornMs: Double

    public init(op: DrawOp, agent: Agent, bornMs: Double) {
        self.op = op
        self.agent = agent
        self.bornMs = bornMs
    }
}

public struct VisibleOp: Equatable {
    public let item: DrawItem
    /// 0...1 draw-in animation progress.
    public let progress: Double
    /// 1 = fully opaque, 0 = gone.
    public let fade: Double

    public init(item: DrawItem, progress: Double, fade: Double) {
        self.item = item
        self.progress = progress
        self.fade = fade
    }
}

/// Port of `client-web/draw.js` `applyDraw` / `visibleOps`. Every method returns a new value.
public struct DrawState: Equatable {
    public static let drawInMs = 250.0
    public static let fadeMs = 350.0
    public static let empty = DrawState(seqs: [:], ops: [:])

    private static let agentOrder: [Agent] = [.helper, .shopper]

    private let seqs: [Agent: Int]
    private let ops: [Agent: [DrawItem]]

    private init(seqs: [Agent: Int], ops: [Agent: [DrawItem]]) {
        self.seqs = seqs
        self.ops = ops
    }

    /// Replaces that agent's ops. `clear` wipes that agent only, same `id` replaces,
    /// a lower `seq` than already seen is ignored (equal is accepted).
    public func applying(agent: Agent, seq: Int, ops incoming: [DrawOp], nowMs: Double) -> DrawState {
        if seq < (seqs[agent] ?? -1) { return self }

        var list = ops[agent] ?? []
        for op in incoming {
            if op.kind == "clear" {
                list = []
                continue
            }
            if op.kind.isEmpty || op.id.isEmpty { continue }
            list = list.filter { $0.op.id != op.id }
            list.append(DrawItem(op: op, agent: agent, bornMs: nowMs))
        }

        var nextSeqs = seqs
        nextSeqs[agent] = seq
        var nextOps = ops
        nextOps[agent] = list
        return DrawState(seqs: nextSeqs, ops: nextOps)
    }

    /// Ops still on screen at `nowMs`, helper first then shopper, in insertion order.
    public func visible(nowMs: Double) -> [VisibleOp] {
        var out: [VisibleOp] = []
        for agent in DrawState.agentOrder {
            for item in ops[agent] ?? [] {
                if DrawState.isExpired(item, nowMs: nowMs) { continue }
                out.append(VisibleOp(
                    item: item,
                    progress: DrawState.progress(of: item, nowMs: nowMs),
                    fade: DrawState.fade(of: item, nowMs: nowMs)
                ))
            }
        }
        return out
    }

    /// A positive `ttlMs`, or nil when the op stays until cleared (missing or 0).
    private static func lifetimeMs(of item: DrawItem) -> Double? {
        guard let ttlMs = item.op.ttlMs, ttlMs > 0 else { return nil }
        return Double(ttlMs)
    }

    private static func isExpired(_ item: DrawItem, nowMs: Double) -> Bool {
        guard let lifetime = lifetimeMs(of: item) else { return false }
        return nowMs - item.bornMs >= lifetime
    }

    private static func fade(of item: DrawItem, nowMs: Double) -> Double {
        guard let lifetime = lifetimeMs(of: item) else { return 1 }
        let age = nowMs - item.bornMs
        if age >= lifetime { return 0 }
        let windowMs = min(fadeMs, lifetime)
        let start = lifetime - windowMs
        if age <= start { return 1 }
        return (lifetime - age) / windowMs
    }

    private static func progress(of item: DrawItem, nowMs: Double) -> Double {
        let age = nowMs - item.bornMs
        if age <= 0 { return 0 }
        if age >= drawInMs { return 1 }
        return age / drawInMs
    }
}
