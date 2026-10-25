import AEyeCore
import SwiftUI
import UIKit

/// The memory-aid answer (`rewind_hit`): the earlier frame with the thing boxed and its call time, beside the
/// live view with an arrow to where the thing is now. Matches `renderSplit` in `client-web/memory.js`:
/// two 4:3 panes, boxes and arrow as fractions of each pane, "Then · m:ss" and "Now" tags.
struct MemorySplitSheet: View {
    let hit: RewindHit
    let controller: CallController
    /// The live picture on the headset: the picked photo. nil while a paired phone is the camera (the server
    /// does not relay phone frames to the headset) or before a photo is picked.
    let livePicture: UIImage?
    @Environment(\.dismiss) private var dismiss

    private static let target = Color(hex: DrawRole.target.hex)

    var body: some View {
        NavigationStack {
            VStack(alignment: .leading, spacing: 14) {
                HStack(spacing: 12) {
                    thenPane
                    nowPane
                }
                TimelineView(.periodic(from: .now, by: 1)) { context in
                    Text(line(at: context.date))
                        .font(.body.weight(.medium))
                        .foregroundStyle(.white)
                }
                Text(Copy.hitCaption(hit))
                    .font(.system(.footnote, design: .monospaced))
                    .foregroundStyle(Color.white.opacity(0.7))
            }
            .padding(20)
            .navigationTitle("Rewind")
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("Close") { dismiss() }
                }
            }
        }
        .frame(minWidth: 860, minHeight: 520)
    }

    /// `Best guess · Seen 2:10 ago` when the server was not sure, as on the web page.
    private func line(at date: Date) -> String {
        let now = controller.callStartedAt.map { date.timeIntervalSince($0) } ?? hit.tS
        let ago = Copy.agoLine(hitS: hit.tS, callNowS: now)
        return hit.sure ? ago : "Best guess · \(ago)"
    }

    private var thenPane: some View {
        pane(tag: "Then · \(Copy.callTime(hit.tS))", tagColor: .white) {
            if let b64 = hit.frameB64, let data = Data(base64Encoded: b64), let image = UIImage(data: data) {
                Image(uiImage: image).resizable()
            } else {
                Color(white: 0.07)
            }
        } overlay: { size in
            ForEach(Array((hit.objects ?? []).enumerated()), id: \.offset) { entry in
                box(entry.element, in: size)
            }
        }
    }

    private var nowPane: some View {
        let live = Copy.matchLive(hit.objects ?? [], controller.liveObjects)
        return pane(tag: "Now", tagColor: Self.target) {
            if let livePicture {
                Image(uiImage: livePicture).resizable()
            } else {
                ZStack {
                    Color(white: 0.07)
                    Text(controller.pairing.joined ? "Phone camera: live video stays on the phone" : "Camera is off")
                        .font(.footnote)
                        .foregroundStyle(Color.white.opacity(0.6))
                        .multilineTextAlignment(.center)
                        .padding()
                }
            }
        } overlay: { size in
            if let live {
                arrow(to: live.bbox, in: size)
                Text("\(live.label) is here")
                    .font(.footnote.weight(.semibold))
                    .foregroundStyle(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(Color.black.opacity(0.55), in: Capsule())
                    .position(x: size.width / 2, y: size.height - 18)
            } else if livePicture != nil {
                Text("Not in view now. Look around slowly.")
                    .font(.footnote)
                    .foregroundStyle(.white)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 4)
                    .background(Color.black.opacity(0.55), in: Capsule())
                    .position(x: size.width / 2, y: size.height - 18)
            }
        }
    }

    /// A 4:3 pane, content stretched to fill so boxes and the arrow are plain fractions of the pane.
    private func pane<Content: View, Overlay: View>(
        tag: String, tagColor: Color,
        @ViewBuilder content: () -> Content,
        @ViewBuilder overlay: @escaping (CGSize) -> Overlay
    ) -> some View {
        content()
            .aspectRatio(4 / 3, contentMode: .fit)
            .overlay {
                GeometryReader { geometry in
                    overlay(geometry.size)
                }
            }
            .overlay(alignment: .topLeading) {
                Text(tag)
                    .font(.system(.footnote, design: .monospaced).weight(.bold))
                    .foregroundStyle(tagColor)
                    .padding(.horizontal, 10)
                    .padding(.vertical, 3)
                    .background(Color.black.opacity(0.55), in: Capsule())
                    .padding(8)
            }
            .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
            .frame(maxWidth: .infinity)
    }

    private func box(_ object: SceneBox, in size: CGSize) -> some View {
        let r = rect(object.bbox, in: size)
        return ZStack(alignment: .topLeading) {
            RoundedRectangle(cornerRadius: 6, style: .continuous)
                .stroke(Self.target, lineWidth: 3)
                .frame(width: r.width, height: r.height)
            Text(object.label)
                .font(.system(size: 13, weight: .medium, design: .monospaced))
                .foregroundStyle(Self.target)
                .shadow(color: .black.opacity(0.7), radius: 2, y: 1)
                .offset(y: -18)
        }
        .position(x: r.midX, y: r.midY)
    }

    /// `memory.js` arrowTo: from the pane's bottom centre to just below the box centre.
    private func arrow(to bbox: [Double], in size: CGSize) -> some View {
        let r = rect(bbox, in: size)
        let from = CGPoint(x: size.width * 0.5, y: size.height * 0.96)
        let to = CGPoint(x: r.midX, y: min(size.height, r.maxY + size.height * 0.02))
        let angle = atan2(to.y - from.y, to.x - from.x)
        let head: CGFloat = 14
        return Path { path in
            path.move(to: from)
            path.addLine(to: to)
            path.move(to: CGPoint(x: to.x - head * cos(angle + 0.5), y: to.y - head * sin(angle + 0.5)))
            path.addLine(to: to)
            path.addLine(to: CGPoint(x: to.x - head * cos(angle - 0.5), y: to.y - head * sin(angle - 0.5)))
        }
        .stroke(Self.target, style: StrokeStyle(lineWidth: 3, lineCap: .round, lineJoin: .round))
        .shadow(color: .black.opacity(0.6), radius: 2)
    }

    private func rect(_ bbox: [Double], in size: CGSize) -> CGRect {
        func at(_ i: Int) -> Double { i < bbox.count ? min(1, max(0, bbox[i])) : 0 }
        return CGRect(x: at(0) * size.width, y: at(1) * size.height, width: at(2) * size.width, height: at(3) * size.height)
    }
}
