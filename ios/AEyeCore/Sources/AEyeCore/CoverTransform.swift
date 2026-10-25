import CoreGraphics
import Foundation

/// Rect clamped so it stays inside `width` x `height`. Negative sizes flip first.
/// Port of `clampRect` in `client-web/overlay.js`.
public func clampRect(x: Double, y: Double, w: Double, h: Double, width: Double, height: Double) -> CGRect {
    var rx = x
    var ry = y
    var rw = w
    var rh = h
    if rw < 0 {
        rx += rw
        rw = -rw
    }
    if rh < 0 {
        ry += rh
        rh = -rh
    }
    if rx < 0 {
        rw += rx
        rx = 0
    }
    if ry < 0 {
        rh += ry
        ry = 0
    }
    if rx > width { rx = width }
    if ry > height { ry = height }
    if rw < 0 { rw = 0 }
    if rh < 0 { rh = 0 }
    if rx + rw > width { rw = max(0, width - rx) }
    if ry + rh > height { rh = max(0, height - ry) }
    return CGRect(x: rx, y: ry, width: rw, height: rh)
}

/// Maps normalised 0..1 coordinates of the sent frame onto a view that shows the frame
/// aspect-fill (`object-fit: cover`). Port of `coverTransform` in `client-web/overlay.js`.
public struct CoverTransform {
    public let scale: Double
    public let offsetX: Double
    public let offsetY: Double
    private let scaledW: Double
    private let scaledH: Double
    private let srcMin: Double
    private let viewW: Double
    private let viewH: Double

    public init(srcW: Double, srcH: Double, viewW: Double, viewH: Double) {
        self.viewW = viewW
        self.viewH = viewH
        self.srcMin = min(srcW, srcH)

        guard srcW > 0, srcH > 0, viewW > 0, viewH > 0 else {
            scale = 1
            offsetX = 0
            offsetY = 0
            scaledW = srcW
            scaledH = srcH
            return
        }

        let ratioW = viewW / srcW
        let ratioH = viewH / srcH
        scale = max(ratioW, ratioH)
        // The axis that limits the scale fills the view exactly. Use the view size directly there so
        // floating-point error cannot leave a sliver (offset 0, normalised 1 maps to the view edge).
        if ratioH >= ratioW {
            scaledH = viewH
            scaledW = srcW * scale
        } else {
            scaledW = viewW
            scaledH = srcH * scale
        }
        offsetX = (viewW - scaledW) / 2
        offsetY = (viewH - scaledH) / 2
    }

    /// `p` is `[x, y]`; missing components count as 0.
    public func point(_ p: [Double]) -> CGPoint {
        let nx = p.count > 0 ? p[0] : 0
        let ny = p.count > 1 ? p[1] : 0
        return CGPoint(x: nx * scaledW + offsetX, y: ny * scaledH + offsetY)
    }

    /// `bbox` is `[x, y, w, h]`, clamped to the view.
    public func rect(_ bbox: [Double]) -> CGRect {
        func at(_ i: Int) -> Double { i < bbox.count ? bbox[i] : 0 }
        return clampRect(
            x: at(0) * scaledW + offsetX,
            y: at(1) * scaledH + offsetY,
            w: at(2) * scaledW,
            h: at(3) * scaledH,
            width: viewW,
            height: viewH
        )
    }

    /// `r` is a fraction of the source's shorter side, scaled like the frame so circles stay round.
    public func radius(_ r: Double) -> Double {
        r * srcMin * scale
    }
}
