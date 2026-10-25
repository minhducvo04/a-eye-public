import SwiftUI
import UIKit

/// Shown instead of a black screen when there is no picture. Copied from the iPhone notice; on Vision Pro the
/// button is passed in (the photo picker), because there is no camera permission to turn on.
struct PermissionNotice<Action: View>: View {
    var symbol: String = "camera.fill"
    let title: String
    let message: String
    @ViewBuilder let action: () -> Action

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
            action()
        }
        .padding(32)
        .frame(maxWidth: 460)
    }
}
