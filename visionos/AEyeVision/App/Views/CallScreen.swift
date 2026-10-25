import AEyeCore
import SwiftUI
import UIKit

/// Chrome drawn over the live camera during a call. The camera preview itself lives in `RootView`, so it
/// never restarts when a call begins or ends.
struct CallScreen: View {
    @Bindable var controller: CallController
    /// The picked photo, for the memory-aid answer's "Now" pane.
    let livePicture: UIImage?

    /// Text being typed in typed-input mode.
    @State private var draft = ""
    @State private var showPairing = false
    @State private var showMemoryActions = false

    var body: some View {
        let state = controller.state

        ZStack {
            // Draw ops, under the controls. It fills the same area as the camera preview (which ignores the
            // safe area), so boxes land on the object they mark.
            GeometryReader { geometry in
                OverlayView(draw: state.draw, frameSize: controller.pairing.joined
                            ? geometry.size : controller.lastFrameSize)
            }
                .ignoresSafeArea()
                .allowsHitTesting(false)
                .accessibilityHidden(true)

            // Viewfinder sweep while the helper reads the picture (iOS c39cb5d).
            TimelineView(.periodic(from: .now, by: 1)) { context in
                if let wait = visibleWait(state, at: context.date), wait.agent == .helper {
                    ScanSweep(color: Palette.helper)
                        .ignoresSafeArea()
                        .transition(.opacity)
                }
            }

            VStack(spacing: 12) {
                topBar(state: state)
                Spacer(minLength: 0)
                waitCard(state: state)
                CaptionCard(caption: state.caption, errorText: state.errorText)
                if controller.isInputResolved, controller.inputMode == .voice {
                    ListeningStrip(listener: controller.listener)
                }
                powerOffGate(state: state)
                if controller.isInputResolved, controller.inputMode == .typed {
                    typedBar
                }
                bottomBar
            }
            .padding(.horizontal, 16)
            .padding(.bottom, 20)

            rewindBadge
        }
        .animation(.easeOut(duration: 0.25), value: state.wait?.turnId)
        .animation(.easeOut(duration: 0.25), value: state.wait == nil)
        .sheet(item: $controller.rewindHit) { hit in
            MemorySplitSheet(hit: hit, controller: controller, livePicture: controller.pairing.joined ? nil : livePicture)
        }
        .onChange(of: controller.pairing.joined) { _, joined in
            if joined { showPairing = false }  // the call screen shows "Phone camera + mic" from here
        }
        #if DEBUG
        .task {
            // -AEyeDebugPair YES: ask for a pairing code once connected and open the Pair phone sheet.
            guard UserDefaults.standard.bool(forKey: "AEyeDebugPair") else { return }
            for _ in 0..<50 where controller.state.link != .live {
                try? await Task.sleep(nanoseconds: 200_000_000)
            }
            controller.requestPairing()
            showPairing = true
        }
        #endif
        .sheet(isPresented: $showPairing) {
            PairSheet(controller: controller)
        }
        .sheet(item: marketBinding) { presentation in
            MarketSheet(market: presentation.response)
        }
    }

    // MARK: - Top

    private func topBar(state: CallState) -> some View {
        VStack(spacing: 8) {
            HStack(spacing: 8) {
                PresenceDot(presence: state.presence)
                Text("A-eye")
                    .fontWeight(.bold)
                if controller.callPrivate {
                    // Same green as the web page's `.private-badge`.
                    Text("Private")
                        .font(.footnote.weight(.semibold))
                        .padding(.horizontal, 8)
                        .padding(.vertical, 2)
                        .background(Color(hex: "#1F6F4A"), in: Capsule())
                }
                separator
                Text(controller.mode?.title ?? "")
                separator
                elapsedText
            }
            .font(.system(.footnote, design: .monospaced).weight(.medium))
            .foregroundStyle(.white)
            .padding(.horizontal, 14)
            .padding(.vertical, 8)
            .glass(Capsule())
            .accessibilityElement(children: .combine)

            if !controller.pairing.status.isEmpty, controller.pairing.offer == nil, !controller.pairing.joined {
                Text(controller.pairing.status).font(.footnote).foregroundStyle(.white)
            }
            if controller.pairing.joined {
                Label("Phone camera + mic", systemImage: "iphone").font(.caption)
            }
            expertChip
            if controller.showsRewindNotice {
                chip(Copy.rewindNotice, color: .white)
            }
            memoryChips
            if state.link != .live {
                Text(state.link == .reconnecting ? "Reconnecting…" : "Connecting…")
                    .font(.system(.footnote, design: .monospaced).weight(.semibold))
                    .foregroundStyle(Palette.warn)
                    .padding(.horizontal, 12)
                    .padding(.vertical, 6)
                    .glass(Capsule())
            }
        }
        .padding(.top, 8)
    }

    /// `EXPERT 2.0 s` while the expert model works, as in the web page's pipeline line (`turn.js`).
    @ViewBuilder
    private var expertChip: some View {
        if let expert = controller.expert {
            TimelineView(.periodic(from: .now, by: 0.1)) { context in
                chip(String(format: "EXPERT %.1f s", max(0, context.date.timeIntervalSince(expert.startedAt))),
                     color: Palette.helper)
            }
            .accessibilityLabel("Asking the expert")
        }
    }

    /// The memory aid: what is remembered today; tap for the two Forget buttons (`client-web/memory.js`).
    @ViewBuilder
    private var memoryChips: some View {
        if let notice = controller.memoryNotice {
            chip(notice, color: .white)
        } else if controller.rewind.on || controller.memoryCount > 0 {
            VStack(spacing: 6) {
                Button {
                    showMemoryActions.toggle()
                } label: {
                    chip(Copy.countLine(controller.memoryCount), color: Color.white.opacity(0.85))
                }
                .buttonStyle(.plain)
                .accessibilityHint("Shows the Forget buttons")
                if showMemoryActions {
                    HStack(spacing: 8) {
                        Button { controller.forget(scope: "last") } label: { chip("Forget that", color: .white) }
                            .buttonStyle(.plain)
                        Button { controller.forget(scope: "today") } label: { chip("Forget today", color: Palette.warn) }
                            .buttonStyle(.plain)
                    }
                }
            }
        }
    }

    private func chip(_ text: String, color: Color) -> some View {
        Text(text)
            .font(.system(.footnote, design: .monospaced).weight(.semibold))
            .foregroundStyle(color)
            .multilineTextAlignment(.center)
            .padding(.horizontal, 12)
            .padding(.vertical, 6)
            .glass(Capsule())
    }

    /// Top right while Rewind is on: "Rewind on" and the Recap button (`#rewind-badge` on the web page).
    @ViewBuilder
    private var rewindBadge: some View {
        if controller.rewind.on {
            VStack {
                HStack(spacing: 6) {
                    Spacer()
                    chip("Rewind on", color: .white)
                    Button { controller.openRecap() } label: { chip("Recap", color: Palette.helper) }
                        .buttonStyle(.plain)
                        .hoverEffect(.highlight)
                        .accessibilityLabel("Recap this call")
                }
                Spacer()
            }
            .padding(.top, 16)
            .padding(.trailing, 20)
        }
    }

    private var separator: some View {
        Text("·").foregroundStyle(Color.white.opacity(0.5)).accessibilityHidden(true)
    }

    private var elapsedText: some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            Text(elapsed(at: context.date))
                .monospacedDigit()
        }
    }

    private func elapsed(at date: Date) -> String {
        guard let start = controller.callStartedAt else { return "0:00" }
        let seconds = max(0, Int(date.timeIntervalSince(start)))
        return String(format: "%d:%02d", seconds / 60, seconds % 60)
    }

    // MARK: - Middle

    /// Percentage card while an agent works on an answer, so a slow answer does not look like a frozen app.
    private func waitCard(state: CallState) -> some View {
        TimelineView(.periodic(from: .now, by: 1)) { context in
            if let wait = visibleWait(state, at: context.date) {
                WaitCard(wait: wait)
                    .transition(.opacity.combined(with: .scale(scale: 0.96)))
            }
        }
    }

    private func visibleWait(_ state: CallState, at date: Date) -> Wait? {
        state.visibleWait(nowMs: date.timeIntervalSince1970 * 1000)
    }

    /// PC Build only: asks the user to confirm the PC is off before the agent goes on.
    /// It comes back by itself after a reconnect because `CallState` clears the confirmation then.
    @ViewBuilder
    private func powerOffGate(state: CallState) -> some View {
        if let mode = controller.mode, state.showsPowerOffButton(for: mode) {
            VStack(spacing: 6) {
                Button {
                    controller.confirmPowerOff()
                } label: {
                    Label("Power is off", systemImage: "bolt.slash")
                        .font(.headline)
                        .foregroundStyle(Color.black)
                        .frame(maxWidth: .infinity)
                        .padding(.vertical, 14)
                        .background(Palette.warn, in: RoundedRectangle(cornerRadius: 16, style: .continuous))
                }
                .accessibilityHint("Confirms the PSU is switched off and unplugged")

                Text("Switch the PSU off and unplug it first.")
                    .font(.footnote.weight(.medium))
                    .foregroundStyle(Color.white.opacity(0.9))
                    .shadow(color: .black.opacity(0.6), radius: 3)
                    .multilineTextAlignment(.center)
            }
        }
    }

    // MARK: - Typed input

    private var typedBar: some View {
        HStack(spacing: 8) {
            TextField(
                "Message",
                text: $draft,
                prompt: Text("Type to A-eye…").foregroundStyle(Color.white.opacity(0.55))
            )
            .foregroundStyle(.white)
            .submitLabel(.send)
            .onSubmit(sendDraft)
            .padding(.leading, 6)

            Button(action: sendDraft) {
                Image(systemName: "arrow.up.circle.fill")
                    .font(.system(size: 30))
                    .foregroundStyle(canSend ? Palette.helper : Color.white.opacity(0.3))
            }
            .disabled(!canSend)
            .accessibilityLabel("Send")
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .glass(Capsule())
    }

    private var canSend: Bool {
        !draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private func sendDraft() {
        guard canSend else { return }
        controller.send(text: draft)
        draft = ""
    }

    // MARK: - Bottom

    private var bottomBar: some View {
        HStack(spacing: 28) {
            Button("Pair phone", systemImage: "qrcode") {
                controller.requestPairing()
                showPairing = true
            }
            .disabled(controller.state.link != .live || controller.pairing.joined)
            rewindButton
            micButton

            Button {
                controller.end()
            } label: {
                Image(systemName: "phone.down.fill")
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 68, height: 68)
                    .background(Palette.endCall, in: Circle())
            }
            .accessibilityLabel("End call")
        }
    }

    /// Rewind on/off, the same state as the Settings switch. Off and disabled in Private mode.
    private var rewindButton: some View {
        let on = controller.rewind.on
        let blocked = controller.callPrivate || controller.rewind.locked
        return Button {
            controller.setRewind(!on)
        } label: {
            Image(systemName: "clock.arrow.circlepath")
                .font(.system(size: 22, weight: .medium))
                .foregroundStyle(on ? Color.black : Color.white)
                .frame(width: 56, height: 56)
                .background(on ? Color.white : Color.clear, in: Circle())
                .glass(Circle())
        }
        .buttonStyle(.plain)
        .hoverEffect(.highlight)
        .disabled(blocked)
        .opacity(blocked ? 0.5 : 1)
        .accessibilityLabel(on ? "Rewind on" : "Rewind off")
        .accessibilityHint(blocked ? "Off in Private mode" : "Keeps clips of this call to ask about later")
    }

    /// Mutes and unmutes on-device listening. Inactive while typing is the input (permission off, or no
    /// on-device speech recognition).
    private var micButton: some View {
        let isVoice = controller.isInputResolved && controller.inputMode == .voice
        let isMuted = controller.listener.isMuted
        return Button {
            controller.toggleMute()
        } label: {
            Image(systemName: isVoice && !isMuted ? "mic.fill" : "mic.slash.fill")
                .font(.system(size: 22, weight: .medium))
                .foregroundStyle(isVoice && isMuted ? Palette.warn : Color.white)
                .frame(width: 56, height: 56)
                .glass(Circle())
        }
        .disabled(!isVoice)
        .opacity(isVoice ? 1 : 0.5)
        .accessibilityLabel("Microphone")
        .accessibilityValue(isVoice ? (isMuted ? "Muted" : "On") : "Off")
        .accessibilityHint(isVoice ? "Double tap to mute or unmute" : "Voice input is off. Use the text field.")
    }

    // MARK: - Market sheet

    /// Presents the shopper's offers while `state.market` is set. Closing the sheet (swipe or button) clears it.
    private var marketBinding: Binding<MarketPresentation?> {
        Binding(
            get: { controller.state.market.map(MarketPresentation.init(response:)) },
            set: { newValue in
                if newValue == nil { controller.dismissMarket() }
            }
        )
    }
}
