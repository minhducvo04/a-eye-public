import AEyeCore
import SwiftUI

/// The last spoken line, on dark glass over the camera, with the server's problem line under it.
/// `isLarge` is the calm Memory look from the web (`body[data-mode="memory"] .caption`): 23 pt text, more
/// padding, reading font for the eyebrow. A problem reads as information: white text with a small amber dot.
struct CaptionCard: View {
    let caption: Caption?
    let errorText: String?
    var isLarge = false

    var body: some View {
        if caption != nil || errorText != nil {
            VStack(alignment: .leading, spacing: 6) {
                if let caption {
                    Text(eyebrow(for: caption.agent))
                        .font(.system(.caption2, design: isLarge ? .default : .monospaced).weight(.bold))
                        .tracking(isLarge ? 0 : 1.2)
                        .foregroundStyle(accent(for: caption.agent))
                    Text(caption.text)
                        .font(isLarge ? .system(size: 23, weight: .medium) : .body.weight(.medium))
                        .lineSpacing(isLarge ? 6 : 3)
                        .foregroundStyle(.white)
                        .lineLimit(8)
                        .fixedSize(horizontal: false, vertical: true)
                }
                if let errorText {
                    HStack(alignment: .firstTextBaseline, spacing: 8) {
                        Circle()
                            .fill(Palette.amber)
                            .frame(width: 8, height: 8)
                            .offset(y: -1)
                        Text(errorText)
                            .font(isLarge ? .body.weight(.medium) : .subheadline.weight(.medium))
                            .foregroundStyle(.white)
                    }
                    .padding(.top, 2)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, isLarge ? 20 : 16)
            .padding(.vertical, isLarge ? 18 : 14)
            .callGlass(RoundedRectangle(cornerRadius: 20, style: .continuous), tint: 0.66)
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
