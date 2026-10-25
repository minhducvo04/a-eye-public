import SwiftUI

/// Recap of the call from the Rewind clips: ordered steps with call times, then what looks skipped for the mode.
/// Same words as `renderRecap` in `client-web/rewind.js`.
struct RecapSheet: View {
    let recap: Recap
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 12) {
                    if recap.pending {
                        HStack(spacing: 10) {
                            ProgressView()
                            Text("Making your recap from the Rewind clips…")
                        }
                    } else if recap.steps.isEmpty {
                        Text(recap.say?.isEmpty == false ? recap.say! : "Nothing to recap yet.")
                    } else {
                        ForEach(Array(recap.steps.enumerated()), id: \.offset) { entry in
                            HStack(alignment: .firstTextBaseline, spacing: 10) {
                                Text("\(entry.offset + 1).")
                                    .foregroundStyle(Color.white.opacity(0.6))
                                Text(Copy.callTime(entry.element.tS))
                                    .font(.system(.body, design: .monospaced).weight(.semibold))
                                    .foregroundStyle(Palette.helper)
                                Text(entry.element.text)
                            }
                        }
                        Text(recap.skipped.isEmpty ? "No typical step looks skipped."
                             : "Maybe skipped: \(recap.skipped.joined(separator: ", "))")
                            .font(.subheadline)
                            .foregroundStyle(recap.skipped.isEmpty ? Color.white.opacity(0.7) : Palette.warn)
                            .padding(.top, 6)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(20)
            }
            .navigationTitle("Recap")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .frame(minWidth: 560, minHeight: 380)
    }
}
