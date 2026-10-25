import SwiftUI

/// Viewfinder brackets with a soft band sweeping down between them, over the live camera while the agent is
/// looking. Tells the user the picture is being read, so they keep the thing in frame. Reduce Motion keeps the
/// brackets and drops the sweep.
struct ScanSweep: View {
    let color: Color

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    private static let period = 1.8
    private static let bandHeight: CGFloat = 90
    private static let bracketLength: CGFloat = 26
    /// The scanned region: inset from the sides, and above the caption and controls at the bottom.
    private static let horizontalInset: CGFloat = 28
    private static let topFraction: CGFloat = 0.08
    private static let bottomFraction: CGFloat = 0.88

    var body: some View {
        GeometryReader { geometry in
            let region = CGRect(
                x: Self.horizontalInset,
                y: geometry.size.height * Self.topFraction,
                width: max(0, geometry.size.width - Self.horizontalInset * 2),
                height: geometry.size.height * (Self.bottomFraction - Self.topFraction)
            )
            ZStack(alignment: .topLeading) {
                if !reduceMotion {
                    TimelineView(.animation) { context in
                        band(in: region, seconds: context.date.timeIntervalSinceReferenceDate)
                    }
                }
                brackets(in: region)
                    .stroke(color.opacity(0.85), style: StrokeStyle(lineWidth: 3, lineCap: .round, lineJoin: .round))
                    .shadow(color: .black.opacity(0.35), radius: 3)
            }
        }
        .allowsHitTesting(false)
        .accessibilityHidden(true)
    }

    /// A gradient band whose bright lower edge travels from the top of the region to the bottom.
    private func band(in region: CGRect, seconds: Double) -> some View {
        let phase = seconds.truncatingRemainder(dividingBy: Self.period) / Self.period
        let edgeY = region.minY + region.height * phase
        let top = max(region.minY, edgeY - Self.bandHeight)
        return LinearGradient(
            colors: [color.opacity(0), color.opacity(0.22), color.opacity(0.55)],
            startPoint: .top,
            endPoint: .bottom
        )
        .frame(width: region.width, height: max(0, edgeY - top))
        .overlay(alignment: .bottom) {
            Rectangle().fill(color).frame(height: 1.5)
        }
        .offset(x: region.minX, y: top)
    }

    private func brackets(in region: CGRect) -> Path {
        let length = min(Self.bracketLength, region.width / 3, region.height / 3)
        var path = Path()
        let corners: [(CGPoint, CGFloat, CGFloat)] = [
            (CGPoint(x: region.minX, y: region.minY), 1, 1),
            (CGPoint(x: region.maxX, y: region.minY), -1, 1),
            (CGPoint(x: region.minX, y: region.maxY), 1, -1),
            (CGPoint(x: region.maxX, y: region.maxY), -1, -1),
        ]
        for (corner, dx, dy) in corners {
            path.move(to: CGPoint(x: corner.x, y: corner.y + dy * length))
            path.addLine(to: corner)
            path.addLine(to: CGPoint(x: corner.x + dx * length, y: corner.y))
        }
        return path
    }
}
