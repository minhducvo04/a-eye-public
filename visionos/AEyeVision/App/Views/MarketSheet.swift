import AEyeCore
import SwiftUI

/// Wraps a `MarketResponse` for `.sheet(item:)`. The id comes from the offers, so the same response keeps the
/// same identity from one body evaluation to the next and the sheet does not reset itself.
struct MarketPresentation: Identifiable {
    let response: MarketResponse

    var id: String {
        ([response.category] + response.items.map { $0.url }).joined(separator: "|")
    }
}

/// The shopper's offers: up to three, each with price, a compatibility chip and a link to the retailer.
/// Compatibility is whatever the server decided; the app only shows it.
struct MarketSheet: View {
    let market: MarketResponse

    private static let maxOffers = 3
    private static let background = Color(hex: "#0C1016")
    private static let fitsColor = Color(hex: DrawRole.ok.hex)
    private static let unverifiedColor = Color(hex: DrawRole.info.hex)
    private static let noColor = Color(hex: DrawRole.warn.hex)

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                header
                if market.items.isEmpty {
                    Text("No matching offers.")
                        .font(.subheadline)
                        .foregroundStyle(Color.white.opacity(0.7))
                }
                ForEach(Array(market.items.prefix(Self.maxOffers).enumerated()), id: \.offset) { entry in
                    OfferCard(offer: entry.element)
                }
                if let note = market.note, !note.isEmpty {
                    Text(note)
                        .font(.footnote)
                        .foregroundStyle(Color.white.opacity(0.65))
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 18)
        }
        .background(Self.background.ignoresSafeArea())
        .presentationDetents([.medium])
        .presentationDragIndicator(.visible)
        .preferredColorScheme(.dark)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text("SHOPPER")
                .font(.system(.caption2, design: .monospaced).weight(.bold))
                .tracking(1.2)
                .foregroundStyle(Palette.shopper)
            Text(market.category.isEmpty ? "Options" : market.category.capitalized)
                .font(.title3.weight(.semibold))
                .foregroundStyle(.white)
        }
        .accessibilityElement(children: .combine)
        .accessibilityAddTraits(.isHeader)
    }

    // MARK: - Offer card

    private struct OfferCard: View {
        let offer: Offer

        var body: some View {
            VStack(alignment: .leading, spacing: 8) {
                HStack(alignment: .firstTextBaseline, spacing: 10) {
                    Text(offer.title)
                        .font(.body.weight(.semibold))
                        .foregroundStyle(.white)
                        .lineLimit(2)
                    Spacer(minLength: 8)
                    StatusChip(status: offer.compat.status)
                }
                Text(offer.retailer)
                    .font(.subheadline)
                    .foregroundStyle(Color.white.opacity(0.7))
                Text(priceText)
                    .font(.system(.body, design: .monospaced).weight(.medium))
                    .foregroundStyle(.white)
                if let link = safeURL {
                    Link(destination: link) {
                        Label("View at \(offer.retailer)", systemImage: "arrow.up.right.square")
                            .font(.subheadline.weight(.semibold))
                    }
                    .tint(Palette.helper)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(14)
            .glass(RoundedRectangle(cornerRadius: 16, style: .continuous))
            .accessibilityElement(children: .contain)
        }

        /// `$12.99`, or `$12.99 + $4.50 shipping` when the server gave a shipping cost.
        private var priceText: String {
            let price = String(format: "$%.2f", offer.priceUsd)
            guard let shipping = offer.shippingUsd else { return price }
            return price + String(format: " + $%.2f shipping", shipping)
        }

        /// Only web links open; anything else the server sends (tel:, file:, custom schemes) is dropped.
        private var safeURL: URL? {
            guard let url = URL(string: offer.url), let scheme = url.scheme?.lowercased(),
                  scheme == "https" || scheme == "http" else { return nil }
            return url
        }
    }

    // MARK: - Status chip

    private struct StatusChip: View {
        let status: String

        var body: some View {
            Text(label)
                .font(.system(.caption, design: .monospaced).weight(.bold))
                .foregroundStyle(color)
                .padding(.horizontal, 9)
                .padding(.vertical, 4)
                .background(color.opacity(0.16), in: Capsule())
                .overlay(Capsule().stroke(color.opacity(0.6), lineWidth: 1))
                .accessibilityLabel("Compatibility: \(label)")
        }

        private var label: String {
            switch status {
            case "fits": return "Fits"
            case "no": return "Does not fit"
            default: return "Unverified"
            }
        }

        private var color: Color {
            switch status {
            case "fits": return MarketSheet.fitsColor
            case "no": return MarketSheet.noColor
            default: return MarketSheet.unverifiedColor
            }
        }
    }
}
