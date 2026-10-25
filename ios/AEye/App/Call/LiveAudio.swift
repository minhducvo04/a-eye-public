import AEyeCore
import AVFoundation
import Observation

/// Used only by the audio tap. Conversion state stays on that serial audio callback.
private nonisolated final class LivePCMConverter: @unchecked Sendable {
    let converter: AVAudioConverter
    let format: AVAudioFormat
    init?(input: AVAudioFormat) {
        guard let format = AVAudioFormat(commonFormat: .pcmFormatInt16, sampleRate: 16000,
                                        channels: 1, interleaved: true),
              let converter = AVAudioConverter(from: input, to: format) else { return nil }
        self.format = format
        self.converter = converter
    }
    func convert(_ input: AVAudioPCMBuffer) -> (Data, Float)? {
        let capacity = AVAudioFrameCount(ceil(Double(input.frameLength) * 16000 / input.format.sampleRate) + 32)
        guard let output = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: capacity) else { return nil }
        var supplied = false
        var error: NSError?
        converter.convert(to: output, error: &error) { _, status in
            if supplied { status.pointee = .noDataNow; return nil }
            supplied = true
            status.pointee = .haveData
            return input
        }
        guard error == nil, output.frameLength > 0, let samples = output.int16ChannelData?[0] else { return nil }
        let count = Int(output.frameLength)
        var sum: Float = 0
        for i in 0..<count { let value = Float(samples[i]) / 32768; sum += value * value }
        return (Data(bytes: samples, count: count * 2), sqrt(sum / Float(count)))
    }
}

/// In-memory mono PCM transport. Shares the call audio session, never the on-device recognizer.
@MainActor
@Observable
final class LiveAudio {
    private(set) var isMuted = false
    private(set) var levels: [Double] = []
    var interim = ""
    private let engine = AVAudioEngine()
    private let player = AVAudioPlayerNode()
    private let format = AVAudioFormat(standardFormatWithSampleRate: 16000, channels: 1)!
    private var hasTap = false
    private var token = 0
    private var playbackToken = 0
    private var pendingBuffers = 0
    private var queuedSamples = 0
    private var receivedFinal = false
    private var utteranceId: String?
    private var gate = LiveAudioGate()
    private var onChunk: ((Data, Bool) -> Void)?
    var onPlaybackFinished: (() -> Void)?
    var onFailure: (() -> Void)?
    private var observer: NSObjectProtocol?

    init() {
        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: format)
    }

    func start(microphone: Bool, onChunk: @escaping (Data, Bool) -> Void) throws {
        stop()
        self.onChunk = onChunk
        isMuted = !microphone
        if microphone { try installTap() }
        try engine.start()
        observer = NotificationCenter.default.addObserver(forName: .AVAudioEngineConfigurationChange,
                                                          object: engine, queue: .main) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, self.onChunk != nil, !self.engine.isRunning else { return }
                self.stop()
                self.onFailure?()
            }
        }
    }

    private func installTap() throws {
        let input = engine.inputNode
        if !engine.isRunning { try input.setVoiceProcessingEnabled(true) }
        let inputFormat = input.outputFormat(forBus: 0)
        guard inputFormat.sampleRate > 0, let converter = LivePCMConverter(input: inputFormat) else {
            throw NSError(domain: "LiveAudio", code: 1)
        }
        let current = token
        input.installTap(onBus: 0, bufferSize: 1600, format: inputFormat) { [weak self] buffer, _ in
            guard let (data, rms) = converter.convert(buffer) else { return }
            Task { @MainActor [weak self] in
                guard let self, self.token == current, !self.isMuted else { return }
                self.levels = appendingLevel(self.levels, normalizedLevel(rms: rms), capacity: SpeechIn.levelHistoryCount)
                let final = self.gate.consume(rms: rms, duration: Double(data.count) / 32000)
                if self.gate.isActive || final { self.onChunk?(data, final) }
            }
        }
        hasTap = true
    }

    func toggleMute() throws {
        token += 1
        gate = LiveAudioGate()
        levels = []
        if isMuted {
            try installTap()
            isMuted = false
        } else {
            if hasTap { engine.inputNode.removeTap(onBus: 0); hasTap = false }
            isMuted = true
            onChunk?(Data(), true)
        }
    }

    func play(_ audio: ServerAudio) throws {
        if utteranceId != audio.utteranceId {
            stopPlayback()
            utteranceId = audio.utteranceId
        }
        let data = audio.pcm
        let count = data.count / 2
        // Bound queued playback to the server's maximum completed turn (60 seconds).
        guard queuedSamples + count <= 960_000 else {
            stopPlayback()
            throw NSError(domain: "LiveAudio", code: 2)
        }
        receivedFinal = receivedFinal || audio.final
        guard count > 0 else {
            if audio.final && pendingBuffers == 0 { onPlaybackFinished?() }
            return
        }
        if !engine.isRunning { try engine.start() }
        guard let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(count)),
              let samples = buffer.floatChannelData?[0] else { return }
        buffer.frameLength = AVAudioFrameCount(count)
        data.withUnsafeBytes { bytes in
            let raw = bytes.bindMemory(to: UInt8.self)
            for i in 0..<count {
                samples[i] = Float(Int16(bitPattern: UInt16(raw[2*i]) | UInt16(raw[2*i+1]) << 8)) / 32768
            }
        }
        pendingBuffers += 1
        queuedSamples += count
        let current = playbackToken
        player.scheduleBuffer(buffer, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            Task { @MainActor [weak self] in
                guard let self, current == self.playbackToken else { return }
                self.pendingBuffers -= 1
                self.queuedSamples -= count
                if self.pendingBuffers == 0 && self.receivedFinal { self.onPlaybackFinished?() }
            }
        }
        if !player.isPlaying { player.play() }
    }

    func stopPlayback() {
        playbackToken += 1
        player.stop()
        pendingBuffers = 0
        queuedSamples = 0
        receivedFinal = false
        utteranceId = nil
    }

    func stop() {
        token += 1
        if let observer { NotificationCenter.default.removeObserver(observer) }
        observer = nil
        if hasTap { engine.inputNode.removeTap(onBus: 0); hasTap = false }
        stopPlayback()
        engine.stop()
        onChunk = nil
        gate = LiveAudioGate()
        levels = []
        interim = ""
        isMuted = false
    }
}
