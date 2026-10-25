import CoreImage.CIFilterBuiltins
import SwiftUI
import UIKit

struct PairSheet: View {
    let controller: CallController
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            TimelineView(.periodic(from: .now, by: 1)) { context in
                VStack(spacing: 16) {
                    if let offer = controller.pairing.offer,
                       let expiry = controller.pairing.expiresAt, expiry > context.date {
                        Text(offer.code ?? "")
                            .font(.system(size: 64, weight: .bold, design: .monospaced))
                            .textSelection(.enabled)
                        if let socketURL = controller.pairingSocketURL,
                           let link = offer.link(socketURL: socketURL) {
                            if let qr = Self.qrCode(link.absoluteString) {
                                Image(uiImage: qr).interpolation(.none).resizable()
                                    .scaledToFit().frame(width: 220, height: 220)
                                    .padding(16).background(.white)
                                    .accessibilityLabel("Scan to pair phone camera")
                            }
                            Text(link.absoluteString).font(.footnote).textSelection(.enabled)
                                .multilineTextAlignment(.center)
                        }
                        Text("Expires in \(max(0, Int(expiry.timeIntervalSince(context.date)))) seconds")
                            .font(.caption)
                    } else {
                        Text(controller.pairing.offer == nil
                             ? controller.pairing.status : "Pairing code expired")
                        if !controller.pairing.joined {
                            Button("Get a new code") { controller.requestPairing() }
                                .disabled(controller.state.link != .live)
                        }
                    }
                }
                .padding(24)
            }
            .navigationTitle("Pair phone camera")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
    }

    /// Entirely on device. Never sends the pairing link or token to a QR service.
    static func qrCode(_ text: String) -> UIImage? {
        let filter = CIFilter.qrCodeGenerator()
        filter.message = Data(text.utf8)
        filter.correctionLevel = "M"
        guard let output = filter.outputImage,
              let image = CIContext().createCGImage(output, from: output.extent) else { return nil }
        return UIImage(cgImage: image)
    }
}
