import Foundation

/// One entry in the camera screen's mode ring. `pack` is the server skill pack id; nil means "coming soon".
public struct Mode: Equatable, Identifiable {
    public let id: String
    public let title: String
    public let symbol: String
    public let pack: String?
    public let requiresPowerOff: Bool

    public var isAvailable: Bool { pack != nil }

    public init(id: String, title: String, symbol: String, pack: String?, requiresPowerOff: Bool) {
        self.id = id
        self.title = title
        self.symbol = symbol
        self.pack = pack
        self.requiresPowerOff = requiresPowerOff
    }
}

public enum Modes {
    /// Ring order mirrors `client-web/agent.js` MODES: the server packs, then the coming-soon modes.
    /// Sink is the `home` pack (the dad flow); Memory is the memory aid.
    public static let all: [Mode] = [
        Mode(id: "general", title: "General", symbol: "sparkles", pack: "general", requiresPowerOff: false),
        Mode(id: "pc_build", title: "PC Build", symbol: "desktopcomputer", pack: "pc_build", requiresPowerOff: true),
        Mode(id: "cooking", title: "Cooking", symbol: "frying.pan", pack: "cooking", requiresPowerOff: false),
        Mode(id: "assemble", title: "Assembly", symbol: "hammer", pack: "assemble", requiresPowerOff: false),
        Mode(id: "memory", title: "Memory", symbol: "mappin.and.ellipse", pack: "memory", requiresPowerOff: false),
        Mode(id: "mechanic", title: "Mechanic", symbol: "car", pack: nil, requiresPowerOff: false),
        Mode(id: "plumbing", title: "Sink", symbol: "drop", pack: "home", requiresPowerOff: false),
        Mode(id: "engineering", title: "Engineering", symbol: "gearshape.2", pack: "general", requiresPowerOff: false),
        Mode(id: "gemini-live", title: "Gemini Live", symbol: "waveform", pack: "general", requiresPowerOff: false),
    ]

    /// Live is centred at launch, as on the web.
    public static let defaultIndex = all.firstIndex { $0.id == "gemini-live" } ?? 0

    /// The `home` pack waits for a task word; the web sends "sink" as soon as the helper first listens.
    public static let homePack = "home"
    public static let homeStartText = "sink"

    public static func startText(for mode: Mode) -> String? {
        if mode.id == "engineering" { return "light an LED" }
        return mode.pack == homePack ? homeStartText : nil
    }
}
