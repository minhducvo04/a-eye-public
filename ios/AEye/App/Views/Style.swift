import SwiftUI

/// Shared colours and the dark-glass surface used by the caption card, pills and buttons.
/// Mirrors `client-web/agent.css` `:root` so both clients read the same.
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
    /// `--calm`: Private and "on" states, the privacy line on the first screen.
    static let calm = Color(hex: "#3DDC97")
    /// `--amber`: something is being kept, or a problem said calmly.
    static let amber = Color(hex: "#FFC25C")
    /// `--sheet-bg` and the first-run backdrop.
    static let sheet = Color(hex: "#0C1016")
    /// The darker call glass tint (`rgba(8, 10, 14, …)`), so text stays readable over a bright lamp or window.
    static let callInk = Color(red: 8 / 255, green: 10 / 255, blue: 14 / 255)

    static let text2 = Color.white.opacity(0.7)
    static let text3 = Color.white.opacity(0.55)
}

extension View {
    /// Dark glass over the live camera: blur, a black tint and a hairline edge (`.glass` on the web).
    func glass<S: Shape>(_ shape: S) -> some View {
        self
            .background(Color.black.opacity(0.32), in: shape)
            .background(.ultraThinMaterial, in: shape)
            .overlay(shape.stroke(Color.white.opacity(0.14), lineWidth: 1))
    }

    /// Call-screen glass: the same surface with the darker tint the web uses during a call
    /// (0.58 for chrome, 0.66 for the caption).
    func callGlass<S: Shape>(_ shape: S, tint: Double = 0.58) -> some View {
        self
            .background(Palette.callInk.opacity(tint), in: shape)
            .background(.ultraThinMaterial, in: shape)
            .overlay(shape.stroke(Color.white.opacity(0.14), lineWidth: 1))
    }
}

/// One badge style under the call pill (`.badge` on the web): a small coloured dot, a short word, dark glass.
struct StatusBadge: View {
    let text: String
    let color: Color

    var body: some View {
        HStack(spacing: 7) {
            Circle()
                .fill(color)
                .frame(width: 7, height: 7)
            Text(text)
                .font(.footnote.weight(.semibold))
                .foregroundStyle(.white)
        }
        .padding(.horizontal, 12)
        .frame(minHeight: 30)
        .background(Palette.callInk.opacity(0.58), in: Capsule())
        .background(.ultraThinMaterial, in: Capsule())
        .overlay(Capsule().stroke(color.opacity(0.45), lineWidth: 1))
        .accessibilityElement(children: .combine)
    }
}
