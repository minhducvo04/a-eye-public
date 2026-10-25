import AEyeCore
import SwiftUI

/// Server address, voice and connection status. Copied from the iPhone sheet; the Voice section is the one
/// addition (system voice by default, or a server voice from `GET /api/voices`).
struct SettingsSheet: View {
    @Bindable var controller: CallController
    @Environment(\.dismiss) private var dismiss
    @State private var voices: [VoiceOption] = []
    @State private var isLoadingVoices = false

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
                    Picker("Voice", selection: $controller.voiceId) {
                        Text("System voice").tag("")
                        ForEach(voices) { voice in
                            Text(voice.label).tag(voice.id)
                        }
                        if !controller.voiceId.isEmpty, !voices.contains(where: { $0.id == controller.voiceId }) {
                            Text(controller.voiceId).tag(controller.voiceId)
                        }
                    }
                } header: {
                    Text("Voice")
                } footer: {
                    Text(voiceHint)
                }

                Section {
                    Toggle("Private mode", isOn: $controller.privateMode)
                } header: {
                    Text("Privacy")
                } footer: {
                    Text(Copy.privateFoot + " Applies from the next call.")
                }

                Section {
                    Toggle("Rewind", isOn: Binding(get: { controller.rewind.on }, set: { controller.setRewind($0) }))
                        .disabled(controller.privateMode || controller.rewind.locked)
                    Toggle("Keep clips after the call",
                           isOn: Binding(get: { controller.rewindKeep }, set: { controller.setRewindKeep($0) }))
                        .disabled(controller.privateMode)
                    Button("Forget this call's clips") { controller.forget() }
                        .disabled(controller.mode == nil)
                    if let notice = controller.memoryNotice {
                        Text(notice).foregroundStyle(.secondary)
                    }
                } header: {
                    Text("Rewind")
                } footer: {
                    Text(rewindFoot)
                }

                Section("Connection") {
                    Text(status)
                }

                Section {
                    Text("The photo you pick goes only to this server and is not saved on the headset.")
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
        .task(id: controller.serverURLString) { await loadVoices() }
    }

    private var rewindFoot: String {
        if controller.privateMode { return "Rewind stays off in Private mode." }
        if controller.rewind.locked { return "This server keeps everything local, so Rewind cannot be turned on." }
        return Copy.rewindNotice + " " + Copy.rewindFoot
    }

    private var voiceHint: String {
        if isLoadingVoices { return "Loading the server's voices…" }
        if voices.isEmpty { return "The system voice speaks on the headset. No server voices found." }
        return controller.voiceId.isEmpty
            ? "The system voice speaks on the headset."
            : "The server speaks with this voice. Applies from the next call."
    }

    private func loadVoices() async {
        guard let url = serverURL(from: controller.serverURLString) else {
            voices = []
            return
        }
        isLoadingVoices = true
        voices = await VoiceCatalog.fetch(socketURL: url)
        isLoadingVoices = false
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
