import SwiftUI
import UIKit

/// Shown instead of a black screen when a permission is denied. Links to the app's page in Settings.
struct PermissionNotice: View {
    var symbol: String = "camera.fill"
    let title: String
    let message: String

    var body: some View {
        VStack(spacing: 16) {
            Image(systemName: symbol)
                .font(.system(size: 34, weight: .regular))
                .foregroundStyle(Color.white.opacity(0.8))
                .accessibilityHidden(true)
            Text(title)
                .font(.title3.weight(.semibold))
                .foregroundStyle(.white)
                .multilineTextAlignment(.center)
            Text(message)
                .font(.body)
                .foregroundStyle(Color.white.opacity(0.75))
                .multilineTextAlignment(.center)
            Button(action: openSettings) {
                Text("Open Settings")
                    .font(.body.weight(.semibold))
                    .foregroundStyle(.black)
                    .padding(.horizontal, 24)
                    .padding(.vertical, 12)
                    .background(Color.white, in: Capsule())
            }
        }
        .padding(32)
        .frame(maxWidth: 420)
    }

    private func openSettings() {
        guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
        UIApplication.shared.open(url)
    }
}
