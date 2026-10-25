import AEyeCore
import AVFoundation
import SwiftUI
import UIKit

/// Launch screen and call screen share one camera preview, so the camera never restarts or flashes black.
struct RootView: View {
    @StateObject private var camera: CameraService
    @State private var controller: CallController
    @State private var ringIndex = Modes.defaultIndex
    @State private var showSettings = false
    @Environment(\.scenePhase) private var scenePhase

    @MainActor
    init() {
        let camera = CameraService()
        _camera = StateObject(wrappedValue: camera)
        _controller = State(initialValue: CallController(camera: camera))
    }

    var body: some View {
        ZStack {
            Color.black.ignoresSafeArea()

            if isCameraBlocked {
                PermissionNotice(
                    title: "Camera access is off",
                    message: "A-eye looks through the camera to point at parts while you work. Turn the camera on for A-eye in Settings."
                )
            } else {
                CameraPreview(session: camera.session, isFront: camera.isFront)
                    .ignoresSafeArea()
            }

            if !isCameraBlocked {
                if controller.mode != nil {
                    CallScreen(controller: controller, camera: camera)
                } else {
                    cameraChrome
                }
            }
        }
        .sheet(isPresented: $showSettings) {
            SettingsSheet(controller: controller)
        }
        .onAppear {
            camera.start()
            autostartIfAsked()
        }
        .onChange(of: scenePhase) { _, phase in
            switch phase {
            case .active: camera.start()
            case .background: camera.stop()
            default: break
            }
        }
        .onChange(of: controller.mode != nil) { _, isInCall in
            // Keep the screen awake while a call is running.
            UIApplication.shared.isIdleTimerDisabled = isInCall
        }
    }

    private var isCameraBlocked: Bool {
        camera.permission == .denied || camera.permission == .restricted
    }

    private var needsServer: Bool {
        controller.socketURL == nil
    }

    // MARK: - First screen

    /// The web's `#picker`: a dark backdrop, the wordmark, what A-eye is, the privacy line, and the ring.
    /// The camera keeps running underneath so the call opens without a black flash.
    private var cameraChrome: some View {
        ZStack {
            backdrop

            VStack(spacing: 0) {
                topRow
                    .padding(.horizontal, 16)
                    .padding(.top, 8)

                Spacer(minLength: 16)

                intro
                    .padding(.horizontal, 24)

                Spacer(minLength: 16)

                ModeRing(index: $ringIndex, onStart: startCall)
                    .padding(.bottom, 24)
            }
        }
    }

    private var backdrop: some View {
        ZStack {
            Palette.sheet.opacity(0.92)
            RadialGradient(colors: [Color.white.opacity(0.07), .clear],
                           center: .init(x: 0.5, y: 0.42), startRadius: 0, endRadius: 420)
        }
        .ignoresSafeArea()
        .allowsHitTesting(false)
    }

    private var topRow: some View {
        ZStack {
            HStack(spacing: 10) {
                Button {
                    showSettings = true
                } label: {
                    Image(systemName: "gearshape")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundStyle(.white)
                        .frame(width: 44, height: 44)
                        .glass(Circle())
                }
                .accessibilityLabel("Settings")

                if needsServer {
                    Button {
                        showSettings = true
                    } label: {
                        Label("Set server", systemImage: "network")
                            .font(.footnote.weight(.semibold))
                            .foregroundStyle(Palette.warn)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 10)
                            .glass(Capsule())
                    }
                }
                Spacer()
            }

            Text("A-eye")
                .font(.subheadline.weight(.semibold))
                .tracking(0.3)
                .foregroundStyle(Palette.text2)
                .accessibilityHidden(true)
        }
        .frame(minHeight: 44)
    }

    private var intro: some View {
        VStack(alignment: .leading, spacing: 0) {
            Text("I look, point,\nand remember.")
                .font(.system(size: 34, weight: .semibold))
                .tracking(-1)
                .lineSpacing(-2)
                .foregroundStyle(.white)
                .accessibilityAddTraits(.isHeader)

            Text("Show me what you are working on, or ask where you left something. Choose a helper below to start.")
                .font(.body)
                .lineSpacing(4)
                .foregroundStyle(Palette.text2)
                .padding(.top, 16)

            Label {
                Text("Video and pictures are not saved unless you turn on Rewind or the memory aid.")
            } icon: {
                Image(systemName: "shield")
                    .font(.system(size: 16, weight: .semibold))
            }
            .font(.subheadline.weight(.semibold))
            .foregroundStyle(Palette.calm)
            .padding(.top, 20)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    /// Screenshot and rehearsal hook, like the web's `?mode=`: `-aeye-start <mode id>` as a launch argument
    /// starts that call at once. Off unless the argument is present.
    private func autostartIfAsked() {
        let arguments = ProcessInfo.processInfo.arguments
        guard let flag = arguments.firstIndex(of: "-aeye-start"), arguments.indices.contains(flag + 1),
              let index = Modes.all.firstIndex(where: { $0.id == arguments[flag + 1] })
        else { return }
        ringIndex = index
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { startCall(Modes.all[index]) }
    }

    private func startCall(_ mode: Mode) {
        guard !needsServer else {
            showSettings = true
            return
        }
        UIImpactFeedbackGenerator(style: .medium).impactOccurred()
        controller.start(mode)
    }
}
