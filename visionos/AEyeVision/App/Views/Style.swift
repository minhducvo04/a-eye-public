import SwiftUI

/// Shared colours and the dark-glass surface used by the caption card, pills and buttons.
extension Color {
    /// `#RRGGBB`, with or without the leading `#`. Anything unparseable becomes black.
    init(hex: String) {
        let digits = hex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        let value = UInt64(digits, radix: 16) ?? 0
        self.init(.sRGB,
                  red: Double((value >> 16) & 0xFF) / 255,
                  green: Double((value >> 8) & 0xFF) / 255,
                  blue: Double(value & 0xFF) / 255,
                  opacity: 1)
    }
}

enum Palette {
    static let warn = Color(hex: "#FF8A3D")
    static let helper = Color(hex: "#8EB6FF")
    static let shopper = Color(hex: "#E8943A")
    static let endCall = Color(red: 0.90, green: 0.22, blue: 0.24)
}

extension View {
    /// Dark glass over the picture: the visionOS glass material, the iPhone's black tint and hairline edge
    /// (`ios/AEye/App/Views/Style.swift` uses `.ultraThinMaterial` in the same place).
    func glass<S: InsettableShape>(_ shape: S) -> some View {
        self
            .background(Color.black.opacity(0.32), in: shape)
            .glassBackgroundEffect(in: shape)
            .overlay(shape.stroke(Color.white.opacity(0.14), lineWidth: 1))
    }
}
