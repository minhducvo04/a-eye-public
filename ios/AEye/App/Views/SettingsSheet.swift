import AEyeCore
import SwiftUI

/// Server address, cloud consent and connection status.
struct SettingsSheet: View {
    @Bindable var controller: CallController
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextField("192.168.1.10:8000", text: $controller.serverURLString)
                        .keyboardType(.URL)
                        .textContentType(.URL)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.done)
                } header: {
                    Text("Server")
                } footer: {
                    Text(hint)
                }

                Section {
                    SecureField("Leave empty on home Wi-Fi", text: $controller.accessToken)
                        .textContentType(.password)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.done)
                } header: {
                    Text("Access token")
                } footer: {
                    Text("Only needed when the server was started with A_EYE_TOKEN, for example over Tailscale. Kept in the Keychain.")
                }

                Section {
                    Toggle("Gemini Live (Google)", isOn: $controller.googleConsent)
                    Toggle("Private mode", isOn: $controller.privateMode)
                } header: {
                    Text("Cloud consent")
                } footer: {
                    Text("Gemini Live sends microphone audio and downscaled camera video through your server to Google. Private mode blocks cloud services. Changing consent ends the active Live call; changing Private mode ends the current call. Start a new call to apply your choice.")
                }

                Section("Connection") {
                    Text(status)
                }

                Section {
                    Text("Camera frames and audio are not saved on the phone. In Gemini Live, your server forwards them to Google only with your consent.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
            }
            .navigationTitle("Settings")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Done") { dismiss() }
                }
            }
        }
        .presentationDetents([.medium, .large])
    }

    /// What `serverURL(from:)` makes of the input, so a typo is visible before a call fails.
    private var hint: String {
        if let url = serverURL(from: controller.serverURLString) {
            return "Will connect to \(url.absoluteString)"
        }
        let isEmpty = controller.serverURLString.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
        return isEmpty ? "No server set. Enter your Mac's address, for example 192.168.1.10:8000." : "That does not look like a server address."
    }

    private var status: String {
        guard controller.mode != nil else { return "Not in a call" }
        switch controller.state.link {
        case .connecting: return "Connecting…"
        case .live: return "Connected"
        case .reconnecting: return "Reconnecting…"
        }
    }
}
