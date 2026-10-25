import AEyeCore
import PhotosUI
import SwiftUI
import UIKit

/// Launch screen and call screen share one picture, so it never flashes when a call begins or ends.
/// Copied from `ios/AEye/App/RootView.swift`; the live camera is replaced by the picked photo (`PhotoSource`).
struct RootView: View {
    @StateObject private var photo: PhotoSource
    @State private var controller: CallController
    @State private var ringIndex = Modes.defaultIndex
    @State private var showSettings = false
    @State private var pickedItem: PhotosPickerItem?
    @Environment(\.scenePhase) private var scenePhase

    @MainActor
    init() {
        let photo = PhotoSource()
        _photo = StateObject(wrappedValue: photo)
        _controller = State(initialValue: CallController(camera: photo))
    }

    var body: some View {
        ZStack {
            Color.black.opacity(0.55)

            if controller.pairing.joined {
                Color(white: 0.12)
            } else if let image = photo.image {
                // Aspect-fill, the same mapping `OverlayView` uses, so boxes land on what they mark.
                Color.clear
                    .overlay(Image(uiImage: image).resizable().scaledToFill())
                    .clipped()
            } else {
                PermissionNotice(
                    symbol: "photo.on.rectangle",
                    title: "The headset camera is closed to apps",
                    message: "Vision Pro gives apps no camera picture. Pick a photo of what you are working on and A-eye will look at that."
                ) {
                    photoPicker(label: "Pick a photo", prominent: true)
                }
            }

            if controller.mode != nil {
                CallScreen(controller: controller, livePicture: photo.image)
            } else {
                cameraChrome
            }
        }
        .clipShape(RoundedRectangle(cornerRadius: 46, style: .continuous))
        .sheet(isPresented: $showSettings) {
            SettingsSheet(controller: controller)
        }
        .sheet(item: $controller.recap) { recap in
            RecapSheet(recap: recap)
        }
        .onChange(of: pickedItem) { _, item in
            guard let item else { return }
            Task { await photo.load(item) }
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background, controller.mode != nil { controller.end() }
        }
        .onChange(of: controller.mode != nil) { _, isInCall in
            // Keep the display awake while a call is running.
            UIApplication.shared.isIdleTimerDisabled = isInCall
        }
        #if DEBUG
        .task { DebugHooks.run(controller: controller, photo: photo) }
        #endif
    }

    private var needsServer: Bool {
        serverURL(from: controller.serverURLString) == nil
    }

    // MARK: - Ring screen

    private var cameraChrome: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Button {
                    showSettings = true
                } label: {
                    Image(systemName: "gearshape.fill")
                        .font(.system(size: 18, weight: .medium))
                        .foregroundStyle(.white)
                        .frame(width: 44, height: 44)
                        .glass(Circle())
                }
                .buttonStyle(.plain)
                .hoverEffect(.highlight)
                .accessibilityLabel("Settings")

                if needsServer {
                    Button {
                        showSettings = true
                    } label: {
                        Label("Set server", systemImage: "network")
                            .font(.system(.footnote, design: .monospaced).weight(.semibold))
                            .foregroundStyle(Palette.warn)
                            .padding(.horizontal, 12)
                            .padding(.vertical, 10)
                            .glass(Capsule())
                    }
                    .buttonStyle(.plain)
                }
                Spacer()
                if photo.image != nil {
                    photoPicker(label: "Change photo", prominent: false)
                }
            }
            .padding(.horizontal, 24)
            .padding(.top, 20)

            Spacer()

            ModeRing(index: $ringIndex, onStart: startCall)
                .padding(.bottom, 28)
        }
    }

    /// The system photo picker. The app sees only the photo the user picks; no library permission is asked.
    func photoPicker(label: String, prominent: Bool) -> some View {
        let title = photo.isLoading ? "Loading…" : label
        return PhotosPicker(selection: $pickedItem, matching: .images, photoLibrary: .shared()) {
            Label(title, systemImage: "photo")
                .font(prominent ? .body.weight(.semibold) : .system(.footnote, design: .monospaced).weight(.semibold))
                .foregroundStyle(prominent ? Color.black : Color.white)
                .padding(.horizontal, prominent ? 24 : 12)
                .padding(.vertical, prominent ? 12 : 10)
                .background(prominent ? Color.white : Color.clear, in: Capsule())
                .glass(Capsule())
        }
        .buttonStyle(.plain)
        .hoverEffect(.highlight)
    }

    private func startCall(_ mode: Mode) {
        guard !needsServer else {
            showSettings = true
            return
        }
        controller.start(mode)
    }
}
