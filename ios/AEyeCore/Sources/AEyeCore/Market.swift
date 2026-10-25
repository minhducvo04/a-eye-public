import Foundation

/// Compatibility verdict decided by the server: `fits`, `no` or `unverified`.
public struct Compat: Decodable, Equatable {
    public let status: String

    public init(status: String) {
        self.status = status
    }
}

public struct Offer: Decodable, Equatable {
    public let title: String
    public let retailer: String
    public let url: String
    public let priceUsd: Double
    public let shippingUsd: Double?
    public let compat: Compat

    public init(title: String, retailer: String, url: String, priceUsd: Double, shippingUsd: Double?, compat: Compat) {
        self.title = title
        self.retailer = retailer
        self.url = url
        self.priceUsd = priceUsd
        self.shippingUsd = shippingUsd
        self.compat = compat
    }
}

public struct MarketResponse: Decodable, Equatable {
    public let category: String
    public let items: [Offer]
    public let note: String?

    public init(category: String, items: [Offer], note: String?) {
        self.category = category
        self.items = items
        self.note = note
    }
}
