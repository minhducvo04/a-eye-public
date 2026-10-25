import AEyeCore
import SwiftUI
import UIKit

// Copied from `ios/AEye/App/Views/ModeRing.swift`. visionOS has no haptics, so the feedback calls are gone,
// and each item gets a hover effect so it lights up under the user's gaze.

/// Horizontal snap ring of helper modes. The centred item is the selection; tapping it starts a call
/// (or shakes, for a "coming soon" mode).
struct ModeRing: View {
    @Binding var index: Int
    let onStart: (Mode) -> Void

    @State private var position: Int?
    @State private var shakes: [String: CGFloat] = [:]
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// Diameter by distance from the centre: 78, then 52 / 42 / 34 for the neighbours.
    private static let diameters: [CGFloat] = [78, 52, 42, 34]
    private static let opacities: [Double] = [1, 0.8, 0.55, 0.38]
    private static let slotWidth: CGFloat = 84
    private static let centreRing: CGFloat = 5

    init(index: Binding<Int>, onStart: @escaping (Mode) -> Void) {
        _index = index
        _position = State(initialValue: index.wrappedValue)
        self.onStart = onStart
    }

    var body: some View {
        VStack(spacing: 12) {
            label
            ring
        }
    }

    private var selectedMode: Mode {
        Modes.all[min(max(index, 0), Modes.all.count - 1)]
    }

    private var label: some View {
        VStack(spacing: 2) {
            Text(selectedMode.title)
                .font(.title3.weight(.semibold))
                .foregroundStyle(.white)
            Text(selectedMode.isAvailable ? "tap to start" : "coming soon")
                .font(.system(.footnote, design: .monospaced))
                .foregroundStyle(Color.white.opacity(0.7))
        }
        .shadow(color: .black.opacity(0.5), radius: 6)
        .accessibilityElement(children: .combine)
    }

    private var ring: some View {
        GeometryReader { geometry in
            ScrollViewReader { proxy in
                ScrollView(.horizontal, showsIndicators: false) {
                    HStack(spacing: 0) {
                        ForEach(Modes.all.indices, id: \.self) { item($0) }
                    }
                    .scrollTargetLayout()
                }
                .contentMargins(.horizontal, max(0, (geometry.size.width - Self.slotWidth) / 2), for: .scrollContent)
                .scrollTargetBehavior(.viewAligned)
                .scrollPosition(id: $position)
                .onAppear { proxy.scrollTo(index, anchor: .center) }
            }
        }
        .frame(height: Self.slotWidth)
        .onChange(of: position) { _, newPosition in
            guard let newPosition, newPosition != index else { return }
            index = newPosition
        }
        .onChange(of: index) { _, newIndex in
            if position != newIndex { position = newIndex }
        }
    }

    private func item(_ i: Int) -> some View {
        let mode = Modes.all[i]
        let distance = min(abs(i - index), Self.diameters.count - 1)
        let diameter = Self.diameters[distance]
        let isCentre = distance == 0
        let dim = mode.isAvailable ? 1.0 : 0.6

        return Button {
            tap(i)
        } label: {
            ZStack {
                Circle()
                    .fill(Color.black.opacity(isCentre ? 0.25 : 0.35))
                    .background(.ultraThinMaterial, in: Circle())
                Circle()
                    .strokeBorder(isCentre ? Color.white : Color.white.opacity(0.25),
                                  lineWidth: isCentre ? Self.centreRing : 1)
                Image(systemName: symbol(for: mode))
                    .font(.system(size: diameter * 0.36, weight: .medium))
                    .foregroundStyle(.white)
            }
            .frame(width: diameter, height: diameter)
            .opacity(Self.opacities[distance] * dim)
            .frame(width: Self.slotWidth, height: Self.slotWidth)
            .modifier(ShakeEffect(animatableData: shakes[mode.id] ?? 0))
        }
        .buttonStyle(.plain)
        .hoverEffect(.highlight)
        .animation(.snappy(duration: 0.25), value: index)
        .accessibilityLabel(mode.title)
        .accessibilityValue(mode.isAvailable ? (isCentre ? "selected" : "") : "coming soon")
        .accessibilityHint(isCentre && mode.isAvailable ? "Starts a call" : "")
    }

    private func tap(_ i: Int) {
        let mode = Modes.all[i]
        if i != index {
            withAnimation(.snappy) { position = i }
        }
        guard mode.isAvailable else {
            if !reduceMotion {
                withAnimation(.linear(duration: 0.4)) { shakes[mode.id, default: 0] += 1 }
            }
            return
        }
        if i == index {
            onStart(mode)
        }
    }

    /// Falls back to a plain glyph if this SF Symbol is missing on the running iOS version.
    private func symbol(for mode: Mode) -> String {
        UIImage(systemName: mode.symbol) == nil ? "circle.grid.2x2" : mode.symbol
    }
}

/// Horizontal wiggle. Back at rest on every whole number, so each `+= 1` plays one shake.
private struct ShakeEffect: GeometryEffect {
    var travel: CGFloat = 8
    var animatableData: CGFloat

    func effectValue(size: CGSize) -> ProjectionTransform {
        ProjectionTransform(CGAffineTransform(translationX: travel * sin(animatableData * .pi * 4), y: 0))
    }
}
