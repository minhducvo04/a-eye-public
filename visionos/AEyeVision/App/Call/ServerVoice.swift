import AEyeCore
import AVFoundation
import Foundation

/// Decoding of the `audio` message, which `AEyeCore` does not have yet (the `hello` that asks for server speech
/// is `Wire.hello` in `CallExtras.swift`).
enum ServerVoiceMessage {
    /// One `audio` message: raw 16 kHz mono PCM16 little-endian for `utteranceId`. nil for anything else.
    static func audio(_ data: Data) -> AudioChunk? {
        guard let p = try? JSONDecoder().decode(AudioPayload.self, from: data), p.type == "audio",
              let pcm = Data(base64Encoded: p.pcm16B64) else { return nil }
        return AudioChunk(utteranceId: p.utteranceId, pcm: pcm, isFinal: p.final ?? false)
    }

    private struct AudioPayload: Decodable {
        let type: String
        let utteranceId: String
        let pcm16B64: String
        let final: Bool?

        enum CodingKeys: String, CodingKey {
            case type, final
            case utteranceId = "utterance_id"
            case pcm16B64 = "pcm16_b64"
        }
    }
}

struct AudioChunk {
    let utteranceId: String
    let pcm: Data
    let isFinal: Bool
}

/// One entry of `GET /api/voices`.
struct VoiceOption: Decodable, Identifiable, Equatable {
    let id: String
    let label: String
    let engine: String
    let `default`: Bool?
}

enum VoiceCatalog {
    /// `GET /api/voices` on the same host as the socket. Empty on any failure (the picker then offers only
    /// the system voice).
    static func fetch(socketURL: URL) async -> [VoiceOption] {
        guard var components = URLComponents(url: socketURL, resolvingAgainstBaseURL: false) else { return [] }
        components.scheme = components.scheme == "wss" ? "https" : "http"
        components.path = "/api/voices"
        components.query = nil
        guard let url = components.url else { return [] }
        var request = URLRequest(url: url)
        request.timeoutInterval = 4
        guard let (data, _) = try? await URLSession(configuration: .ephemeral).data(for: request),
              let body = try? JSONDecoder().decode(VoiceList.self, from: data) else { return [] }
        return body.voices
    }

    private struct VoiceList: Decodable {
        let voices: [VoiceOption]
    }
}

/// Plays the server's PCM chunks in order on one engine. Memory only; nothing is written or logged.
/// Buffer completions arrive on an audio thread and are handed to `onDrained` on the main queue.
@MainActor
final class PCMPlayer {
    static let sampleRate = 16_000.0

    private let engine = AVAudioEngine()
    private let node = AVAudioPlayerNode()
    private let format = AVAudioFormat(standardFormatWithSampleRate: PCMPlayer.sampleRate, channels: 1)!
    private var isAttached = false
    /// Bumped by `stop()`, so completions of flushed buffers are ignored.
    private var generation = 0

    /// Queues one chunk. `completion` runs on the main actor once the chunk has played (not after `stop()`).
    func enqueue(_ pcm: Data, completion: @escaping @MainActor () -> Void) {
        guard let buffer = makeBuffer(pcm) else {
            completion()
            return
        }
        startIfNeeded()
        let sent = generation
        node.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    guard let self, sent == self.generation else { return }
                    completion()
                }
            }
        }
        if !node.isPlaying { node.play() }
    }

    func stop() {
        generation += 1
        node.stop()
    }

    private func startIfNeeded() {
        if !isAttached {
            engine.attach(node)
            engine.connect(node, to: engine.mainMixerNode, format: format)
            isAttached = true
        }
        if !engine.isRunning {
            try? engine.start()
        }
    }

    /// PCM16 little-endian to Float32. An odd trailing byte is dropped.
    private func makeBuffer(_ pcm: Data) -> AVAudioPCMBuffer? {
        let frames = pcm.count / 2
        guard frames > 0, let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)),
              let out = buffer.floatChannelData?[0] else { return nil }
        pcm.withUnsafeBytes { raw in
            for i in 0..<frames {
                let sample = Int16(littleEndian: raw.loadUnaligned(fromByteOffset: i * 2, as: Int16.self))
                out[i] = Float(sample) / 32768
            }
        }
        buffer.frameLength = AVAudioFrameCount(frames)
        return buffer
    }
}
