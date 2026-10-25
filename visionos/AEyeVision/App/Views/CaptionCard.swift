import AEyeCore
import SwiftUI

/// The last spoken line, on dark glass over the camera, with the server's error text under it in the warn colour.
struct CaptionCard: View {
    let caption: Caption?
    let errorText: String?

    var body: some View {
        if caption != nil || errorText != nil {
            VStack(alignment: .leading, spacing: 6) {
                if let caption {
                    Text(eyebrow(for: caption.agent))
                        .font(.system(.caption2, design: .monospaced).weight(.bold))
                        .tracking(1.2)
                        .foregroundStyle(accent(for: caption.agent))
                    Text(caption.text)
                        .font(.body.weight(.medium))
                        .foregroundStyle(.white)
                        .lineLimit(8)
                }
                if let errorText {
                    Text(errorText)
                        .font(.subheadline.weight(.medium))
                        .foregroundStyle(Palette.warn)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)
            .padding(.vertical, 14)
            .glass(RoundedRectangle(cornerRadius: 20, style: .continuous))
            .accessibilityElement(children: .combine)
        }
    }

    private func eyebrow(for agent: Agent) -> String {
        agent == .shopper ? "SHOPPER" : "A-EYE"
    }

    private func accent(for agent: Agent) -> Color {
        agent == .shopper ? Palette.shopper : Palette.helper
    }
}
