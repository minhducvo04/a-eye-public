import AEyeCore
import SwiftUI

/// Draws the server's `draw` ops over the camera. Port of `paintDraw` / `paintOne` in `client-web/draw.js`.
///
/// Ops are 0..1 of the frame that was sent, so each one goes through `CoverTransform` (aspect-fill, the same
/// as the camera preview). The view must therefore fill exactly the area the preview fills: the caller applies
/// `.ignoresSafeArea()` to it, as `RootView` does to the preview.
struct OverlayView: View {
    let draw: DrawState
    /// Pixel size of the frame the draw coordinates refer to. Zero until the first frame has been sent.
    let frameSize: CGSize

    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    // The frame clock only runs while something is actually moving (drawing in, or fading out before its ttl).
    // A settled overlay is drawn once, and an explicit timeline wakes the view at the next moment that changes
    // (an op finishing its draw-in, starting its fade, or expiring). A new `draw` re-evaluates `body`.
    private static let settleMarginMs = 20.0
    private static let fadeLeadMs = 100.0
    private static let expiryMarginMs = 20.0

    var body: some View {
        let now = Date()
        let visible = draw.visible(nowMs: Self.ms(now))
        if visible.isEmpty || frameSize.width <= 0 || frameSize.height <= 0 {
            Color.clear
        } else {
            TimelineView(.explicit(wakeDates(for: visible, from: now))) { wake in
                content(at: wake.date)
            }
        }
    }

    /// Animated while any op moves, a single static render otherwise.
    @ViewBuilder
    private func content(at date: Date) -> some View {
        let nowMs = Self.ms(date)
        if Self.isAnimating(draw.visible(nowMs: nowMs), nowMs: nowMs, reduceMotion: reduceMotion) {
            TimelineView(.animation) { frame in
                canvas(at: frame.date)
            }
        } else {
            canvas(at: date)
        }
    }

    private func canvas(at date: Date) -> some View {
        // Same clock as `CallController.nowMs()` (ms since 1970), taken from the timeline date so every
        // frame is a pure function of its date.
        let visible = draw.visible(nowMs: Self.ms(date))
        let skipAnimation = reduceMotion
        let source = frameSize
        return Canvas { context, size in
            let transform = CoverTransform(
                srcW: source.width, srcH: source.height,
                viewW: size.width, viewH: size.height
            )
            for op in visible {
                OverlayPainter.paint(op, in: context, transform: transform, size: size,
                                     skipAnimation: skipAnimation)
            }
        }
    }

    private static func ms(_ date: Date) -> Double {
        date.timeIntervalSince1970 * 1000
    }

    /// True while any visible op is drawing in, or is inside (or about to enter) its ttl fade-out.
    private static func isAnimating(_ visible: [VisibleOp], nowMs: Double, reduceMotion: Bool) -> Bool {
        visible.contains { op in
            if !reduceMotion && op.progress < 1 { return true }
            guard let ttl = op.item.op.ttlMs, ttl > 0 else { return false }
            let fadeStartMs = Double(ttl) - min(DrawState.fadeMs, Double(ttl))
            return nowMs - op.item.bornMs >= fadeStartMs - fadeLeadMs
        }
    }

    /// `now`, then each upcoming moment the picture changes by itself, oldest first. Op ids never change a
    /// moment: only draw-in completion, fade start (a little early, so rounding cannot skip it) and expiry.
    private func wakeDates(for visible: [VisibleOp], from now: Date) -> [Date] {
        let nowMs = Self.ms(now)
        var upcomingMs: [Double] = []
        for op in visible {
            let born = op.item.bornMs
            if !reduceMotion && op.progress < 1 {
                upcomingMs.append(born + DrawState.drawInMs + Self.settleMarginMs)
            }
            if let ttl = op.item.op.ttlMs, ttl > 0 {
                let fadeStartMs = Double(ttl) - min(DrawState.fadeMs, Double(ttl))
                upcomingMs.append(born + fadeStartMs - Self.fadeLeadMs / 2)
                upcomingMs.append(born + Double(ttl) + Self.expiryMarginMs)
            }
        }
        let later = upcomingMs.filter { $0 > nowMs }.sorted().map { Date(timeIntervalSince1970: $0 / 1000) }
        return [now] + later
    }
}

/// Stateless drawing code, one `GraphicsContext` copy per op so opacity, filters and clips never leak.
private enum OverlayPainter {
    static let lineWidth: CGFloat = 3
    static let strokeStyle = StrokeStyle(lineWidth: lineWidth, lineCap: .round, lineJoin: .round)
    static let labelFontSize: CGFloat = 15
    static let boxCornerRadius: CGFloat = 10
    static let highlightCornerRadius: CGFloat = 12
    static let highlightGlowRadius: CGFloat = 22
    static let highlightFillAlpha = 0.28
    static let boxLabelAfterProgress = 0.4
    static let arrowHeadAfterProgress = 0.72
    static let pillHeight: CGFloat = 28
    static let pillPadX: CGFloat = 10
    static let pillInset: CGFloat = 8
    static let pillFill = Color(.sRGB, red: 12 / 255, green: 16 / 255, blue: 22 / 255, opacity: 0.88)
    static let pillText = Color(hex: "#F4F7FB")
    static let pinDrop: CGFloat = 18

    static func paint(_ visible: VisibleOp, in base: GraphicsContext, transform: CoverTransform,
                      size: CGSize, skipAnimation: Bool) {
        let op = visible.item.op
        let color = Color(hex: (op.role ?? .info).hex)
        let progress = skipAnimation ? 1 : visible.progress
        var context = base
        context.opacity = visible.fade

        switch op.kind {
        case "box":
            paintBox(op, color: color, progress: progress, transform: transform, in: context)
        case "highlight":
            paintHighlight(op, color: color, progress: progress, transform: transform, in: context)
        case "circle":
            paintCircle(op, color: color, progress: progress, transform: transform, in: context)
        case "arrow":
            paintArrow(op, color: color, progress: progress, transform: transform, in: context)
        case "path":
            paintPath(op, color: color, progress: progress, transform: transform, in: context)
        case "label":
            paintLabel(op, color: color, progress: progress, transform: transform, size: size, in: context)
        case "pin":
            paintPin(op, color: color, progress: progress, transform: transform, in: context)
        default:
            break  // unknown kinds (and `clear`, which DrawState already consumed) draw nothing
        }
    }

    // MARK: - Shapes

    private static func paintBox(_ op: DrawOp, color: Color, progress: Double, transform: CoverTransform,
                                 in context: GraphicsContext) {
        guard let bbox = op.bbox else { return }
        let rect = transform.rect(bbox)
        guard rect.width > 0, rect.height > 0 else { return }

        let radius = min(boxCornerRadius, rect.width / 2, rect.height / 2)
        let outline = Path(roundedRect: rect, cornerRadius: radius)
        let drawn = progress < 1 ? outline.trimmedPath(from: 0, to: progress) : outline
        context.stroke(drawn, with: .color(color), style: strokeStyle)

        guard let label = op.label, !label.isEmpty, progress > boxLabelAfterProgress else { return }
        let text = Text(label)
            .font(.system(size: labelFontSize, weight: .medium, design: .monospaced))
            .foregroundStyle(color)
        let anchor = CGPoint(x: rect.minX, y: max(16, rect.minY - 4))
        context.drawLayer { layer in
            // A soft dark edge keeps the label readable over a bright scene.
            layer.addFilter(.shadow(color: .black.opacity(0.7), radius: 2, x: 0, y: 1))
            layer.draw(text, at: anchor, anchor: .bottomLeading)
        }
    }

    private static func paintHighlight(_ op: DrawOp, color: Color, progress: Double, transform: CoverTransform,
                                       in context: GraphicsContext) {
        guard let bbox = op.bbox else { return }
        let rect = transform.rect(bbox)
        guard rect.width > 0, rect.height > 0 else { return }

        let radius = min(highlightCornerRadius, rect.width / 2, rect.height / 2)
        let shape = Path(roundedRect: rect, cornerRadius: radius)
        context.drawLayer { layer in
            layer.addFilter(.shadow(color: color, radius: highlightGlowRadius * CGFloat(progress), x: 0, y: 0))
            layer.fill(shape, with: .color(color.opacity(highlightFillAlpha * progress)))
        }
    }

    private static func paintCircle(_ op: DrawOp, color: Color, progress: Double, transform: CoverTransform,
                                    in context: GraphicsContext) {
        guard let center = op.center, let r = op.r else { return }
        let radius = max(0, transform.radius(r))
        guard radius > 0 else { return }
        let c = transform.point(center)
        let full = Path(ellipseIn: CGRect(x: c.x - radius, y: c.y - radius, width: radius * 2, height: radius * 2))
        let drawn = progress < 1 ? full.trimmedPath(from: 0, to: progress) : full
        context.stroke(drawn, with: .color(color), style: strokeStyle)
    }

    private static func paintArrow(_ op: DrawOp, color: Color, progress: Double, transform: CoverTransform,
                                   in context: GraphicsContext) {
        guard let fromPoint = op.from, let toPoint = op.to else { return }
        let from = transform.point(fromPoint)
        let to = transform.point(toPoint)
        let dx = to.x - from.x
        let dy = to.y - from.y
        let length = hypot(dx, dy)
        guard length >= 0.5 else { return }

        let p = CGFloat(progress)
        let tip = CGPoint(x: from.x + dx * p, y: from.y + dy * p)
        var shaft = Path()
        shaft.move(to: from)
        shaft.addLine(to: tip)
        context.stroke(shaft, with: .color(color), style: strokeStyle)

        guard progress >= arrowHeadAfterProgress else { return }
        let angle = atan2(dy, dx)
        let head = max(10, min(18, length * 0.12))
        let spread = CGFloat.pi / 7
        var arrowHead = Path()
        arrowHead.move(to: tip)
        arrowHead.addLine(to: CGPoint(x: tip.x - head * cos(angle - spread), y: tip.y - head * sin(angle - spread)))
        arrowHead.addLine(to: CGPoint(x: tip.x - head * cos(angle + spread), y: tip.y - head * sin(angle + spread)))
        arrowHead.closeSubpath()
        context.fill(arrowHead, with: .color(color))
    }

    private static func paintPath(_ op: DrawOp, color: Color, progress: Double, transform: CoverTransform,
                                  in context: GraphicsContext) {
        let points = (op.points ?? []).map { transform.point($0) }
        let cut = cutPolyline(points, fraction: CGFloat(progress))
        guard cut.count >= 2 else { return }
        context.stroke(smoothPath(cut), with: .color(color), style: strokeStyle)
    }

    private static func paintLabel(_ op: DrawOp, color: Color, progress: Double, transform: CoverTransform,
                                   size: CGSize, in base: GraphicsContext) {
        guard let label = op.text, !label.isEmpty else { return }
        var context = base
        let at = transform.point(op.at ?? [])

        let resolved = context.resolve(
            Text(label)
                .font(.system(size: labelFontSize, weight: .medium))
                .foregroundStyle(pillText)
        )
        let measured = resolved.measure(in: CGSize(width: 10_000, height: 100))
        let width = min(size.width - pillInset * 2, measured.width + pillPadX * 2)
        let height = pillHeight
        let x = max(pillInset, min(at.x - width / 2, size.width - width - pillInset))
        let y = max(pillInset, min(at.y - height / 2, size.height - height - pillInset))
        let pill = Path(roundedRect: CGRect(x: x, y: y, width: width, height: height), cornerRadius: height / 2)

        context.opacity = base.opacity * (0.25 + 0.75 * progress)
        context.fill(pill, with: .color(pillFill))
        context.stroke(pill, with: .color(color), lineWidth: lineWidth)
        // A label longer than the screen is clipped to the pill rather than spilling out.
        context.clip(to: pill)
        context.draw(resolved, at: CGPoint(x: x + width / 2, y: y + height / 2), anchor: .center)
    }

    private static func paintPin(_ op: DrawOp, color: Color, progress: Double, transform: CoverTransform,
                                 in context: GraphicsContext) {
        guard let location = op.at else { return }
        let at = transform.point(location)
        let x = at.x
        let y = at.y + (1 - CGFloat(progress)) * pinDrop

        var tail = Path()
        tail.move(to: CGPoint(x: x, y: y))
        tail.addLine(to: CGPoint(x: x - 6, y: y - 16))
        tail.addLine(to: CGPoint(x: x + 6, y: y - 16))
        tail.closeSubpath()
        context.fill(tail, with: .color(color))
        context.fill(Path(ellipseIn: CGRect(x: x - 7, y: y - 25, width: 14, height: 14)), with: .color(color))
    }

    // MARK: - Polyline helpers (ports of cutPolyline / strokeSmooth)

    /// The first `fraction` of the polyline's length, ending part-way along a segment.
    static func cutPolyline(_ points: [CGPoint], fraction: CGFloat) -> [CGPoint] {
        guard let first = points.first else { return [] }
        if fraction >= 1 || points.count == 1 { return points }

        var segments: [CGFloat] = []
        var total: CGFloat = 0
        for i in 1..<points.count {
            let d = hypot(points[i].x - points[i - 1].x, points[i].y - points[i - 1].y)
            segments.append(d)
            total += d
        }
        if total <= 0 { return [first] }

        let want = total * max(0, fraction)
        var out = [first]
        var walked: CGFloat = 0
        for i in 1..<points.count {
            let d = segments[i - 1]
            if walked + d >= want {
                let t = d == 0 ? 0 : (want - walked) / d
                out.append(CGPoint(x: points[i - 1].x + (points[i].x - points[i - 1].x) * t,
                                   y: points[i - 1].y + (points[i].y - points[i - 1].y) * t))
                return out
            }
            walked += d
            out.append(points[i])
        }
        return out
    }

    /// Catmull-Rom style curve through the points, as `strokeSmooth` does.
    static func smoothPath(_ points: [CGPoint]) -> Path {
        var path = Path()
        guard points.count >= 2 else { return path }
        path.move(to: points[0])
        if points.count == 2 {
            path.addLine(to: points[1])
            return path
        }
        for i in 0..<(points.count - 1) {
            let p0 = points[i == 0 ? 0 : i - 1]
            let p1 = points[i]
            let p2 = points[i + 1]
            let p3 = i + 2 < points.count ? points[i + 2] : p2
            path.addCurve(
                to: p2,
                control1: CGPoint(x: p1.x + (p2.x - p0.x) / 6, y: p1.y + (p2.y - p0.y) / 6),
                control2: CGPoint(x: p2.x - (p3.x - p1.x) / 6, y: p2.y - (p3.y - p1.y) / 6)
            )
        }
        return path
    }
}
