import Accelerate
import AEyeCore
import AVFoundation
import Foundation
import Observation
import Speech

/// The audio engine and the recognition requests, off the main actor.
///
/// Both the microphone tap and the recognizer's result handler are called on threads of their own, so this class
/// is `nonisolated` on purpose (same pattern as `SocketClient`): it must not pick up an implicit `@MainActor`
/// when the project's Default Actor Isolation is MainActor. Mutable state is guarded by `lock`, and every event
/// is delivered to `onEvent` on the main queue.
///
/// Recognition is on-device only (`requiresOnDeviceRecognition`). Audio goes from the tap straight into the
/// request in memory. It is never written to disk, never logged and never sent anywhere.
private nonisolated final class RecognitionPipeline: @unchecked Sendable {
    enum Event: Sendable {
        case partial(String)
        case finished(String)
        /// One request failed; listening carries on after a pause.
        case failed
        /// Too many requests in a row failed straight away. Listening has stopped; use typed input.
        case gaveUp
        /// RMS loudness of one mic buffer, 0...1. Only this number leaves the tap, for the waveform.
        case level(Float)
    }

    enum PipelineError: Error {
        case noInput
    }

    /// Quiet time after the last partial result before the utterance is closed with `endAudio()`.
    private static let silenceSeconds = 1.2
    /// How long to wait for the final result after `endAudio()` before starting over.
    private static let finalizeTimeoutSeconds = 3.0
    /// Pause before the first failed request is replaced. It doubles for each further failure in a row
    /// (0.5, 1, 2, 4 s), so a recognizer that keeps failing cannot spin.
    private static let restartDelaySeconds = 0.5
    private static let maxRestartDelaySeconds = 4.0
    /// This many failures in a row, each with no partial result, and listening stops for good.
    private static let maxConsecutiveFailures = 5
    /// A request that dies sooner than this without hearing anything counts as a failure (Dictation off, model
    /// missing). A request that ran longer and then ended is the normal silence timeout, not a failure.
    private static let immediateFailureSeconds = 3.0
    private static let tapBufferSize: AVAudioFrameCount = 1024

    private let recognizer: SFSpeechRecognizer
    private let onEvent: @Sendable (Event) -> Void
    private let engine = AVAudioEngine()
    private let queue = DispatchQueue(label: "aeye.speechin")

    private let lock = NSLock()
    private var isActive = false
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    private var token = 0
    private var silenceTimer: DispatchWorkItem?
    // Per request, also guarded by `lock`.
    private var requestBeganAt = Date()
    private var lastPartial = ""
    private var heardPartial = false
    private var consecutiveFailures = 0

    // Only touched from the main thread (start / stop / notifications delivered on main).
    private var observers: [NSObjectProtocol] = []

    init(recognizer: SFSpeechRecognizer, onEvent: @escaping @Sendable (Event) -> Void) {
        self.recognizer = recognizer
        self.onEvent = onEvent
    }

    deinit {
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
    }

    // MARK: - Start / stop (main thread)

    func start() throws {
        lock.lock()
        let wasActive = isActive
        isActive = true
        lock.unlock()
        guard !wasActive else { return }

        do {
            try startEngine()
        } catch {
            lock.lock()
            isActive = false
            lock.unlock()
            throw error
        }
        beginRequest()
        observeInterruptions()
    }

    func stop() {
        lock.lock()
        isActive = false
        lock.unlock()
        retireRequest()
        engine.inputNode.removeTap(onBus: 0)
        engine.stop()
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
        observers = []
    }

    // MARK: - Engine

    private func startEngine() throws {
        let input = engine.inputNode
        // `.voiceChat` on the session does not turn on echo cancellation for an AVAudioEngine input by itself;
        // voice processing has to be enabled on the node, while the engine is stopped. If it cannot be enabled
        // we listen without it (the word-set echo rule in `isBargeIn` is still the backstop). Enabling can change
        // the input format, so the format is read only after this.
        do {
            try input.setVoiceProcessingEnabled(true)
        } catch {
            // Continue without hardware echo cancellation. Nothing about the audio is logged.
        }
        let format = input.outputFormat(forBus: 0)
        guard format.sampleRate > 0, format.channelCount > 0 else { throw PipelineError.noInput }
        input.removeTap(onBus: 0)
        input.installTap(onBus: 0, bufferSize: Self.tapBufferSize, format: format) { [weak self] buffer, _ in
            self?.append(buffer)
        }
        engine.prepare()
        try engine.start()
    }

    /// Runs on the audio thread. `append` is safe to call from any thread.
    private func append(_ buffer: AVAudioPCMBuffer) {
        lock.lock()
        let current = request
        lock.unlock()
        current?.append(buffer)
        emit(.level(Self.rms(of: buffer)))
    }

    /// Loudness of the first channel. The samples themselves are not kept.
    private static func rms(of buffer: AVAudioPCMBuffer) -> Float {
        guard let samples = buffer.floatChannelData?[0], buffer.frameLength > 0 else { return 0 }
        var value: Float = 0
        vDSP_rmsqv(samples, 1, &value, vDSP_Length(buffer.frameLength))
        return value
    }

    /// A route change or a finished interruption (phone call, Siri) can stop the engine. Start it again.
    private func observeInterruptions() {
        let center = NotificationCenter.default
        let restart: @Sendable (Notification) -> Void = { [weak self] _ in self?.restartEngineIfNeeded() }
        observers = [
            center.addObserver(forName: .AVAudioEngineConfigurationChange, object: engine, queue: .main, using: restart),
            center.addObserver(forName: AVAudioSession.interruptionNotification, object: nil, queue: .main) { [weak self] note in
                let raw = note.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt
                if raw == AVAudioSession.InterruptionType.ended.rawValue { self?.restartEngineIfNeeded() }
            },
        ]
    }

    private func restartEngineIfNeeded() {
        lock.lock()
        let active = isActive
        lock.unlock()
        guard active, !engine.isRunning else { return }
        try? AVAudioSession.sharedInstance().setActive(true)
        do {
            try startEngine()
        } catch {
            emit(.failed)
        }
    }

    // MARK: - Recognition requests

    private func beginRequest() {
        let newRequest = SFSpeechAudioBufferRecognitionRequest()
        newRequest.shouldReportPartialResults = true
        newRequest.requiresOnDeviceRecognition = true

        lock.lock()
        guard isActive, request == nil else {
            lock.unlock()
            return
        }
        token += 1
        let current = token
        request = newRequest
        requestBeganAt = Date()
        lastPartial = ""
        heardPartial = false
        lock.unlock()

        let newTask = recognizer.recognitionTask(with: newRequest) { [weak self] result, error in
            // Pull plain values out here; the result object itself is not handed on.
            let text = result?.bestTranscription.formattedString
            self?.handle(text: text, isFinal: result?.isFinal ?? false, hadError: error != nil, token: current)
        }

        lock.lock()
        if token == current && isActive {
            task = newTask
            lock.unlock()
        } else {
            lock.unlock()
            newTask.cancel()
        }
    }

    private func handle(text: String?, isFinal: Bool, hadError: Bool, token current: Int) {
        lock.lock()
        let isCurrent = isActive && current == token
        lock.unlock()
        guard isCurrent else { return }

        if let text {
            lock.lock()
            if !text.isEmpty {
                consecutiveFailures = 0  // the recognizer is working
                if !isFinal {
                    lastPartial = text
                    heardPartial = true
                }
            }
            lock.unlock()

            if isFinal {
                retireRequest()
                emit(.finished(text))
                // An empty final means the recognizer ended without hearing anything; restarting at once could spin.
                scheduleRestart(after: text.isEmpty ? Self.restartDelaySeconds : 0)
            } else {
                emit(.partial(text))
                armSilenceTimer(token: current)
            }
        } else if hadError {
            handleFailure()
        }
    }

    /// A request ended with an error. Back off, and give up after too many immediate failures in a row.
    /// Whatever the user had said so far is delivered first as a finished utterance, so it is not lost.
    private func handleFailure() {
        lock.lock()
        let livedSeconds = Date().timeIntervalSince(requestBeganAt)
        let isImmediate = !heardPartial && livedSeconds < Self.immediateFailureSeconds
        consecutiveFailures = isImmediate ? consecutiveFailures + 1 : 0
        let failures = consecutiveFailures
        let salvaged = lastPartial
        let shouldGiveUp = failures >= Self.maxConsecutiveFailures
        if shouldGiveUp { isActive = false }
        lock.unlock()

        retireRequest()
        if !salvaged.isEmpty { emit(.finished(salvaged)) }
        if shouldGiveUp {
            emit(.gaveUp)
            return
        }
        emit(.failed)
        // 1st failure in a row: 0.5 s, then 1, 2, 4. A failure that was not "immediate" (failures == 0) waits 0.5 s.
        let doublings = Double(max(0, failures - 1))
        scheduleRestart(after: min(Self.restartDelaySeconds * pow(2, doublings), Self.maxRestartDelaySeconds))
    }

    /// Drops the current request and task. Late callbacks from them carry an old token and are ignored.
    private func retireRequest() {
        lock.lock()
        token += 1
        let oldRequest = request
        let oldTask = task
        let timer = silenceTimer
        request = nil
        task = nil
        silenceTimer = nil
        lock.unlock()

        timer?.cancel()
        oldRequest?.endAudio()
        oldTask?.cancel()
    }

    private func scheduleRestart(after delay: TimeInterval) {
        queue.asyncAfter(deadline: .now() + delay) { [weak self] in self?.beginRequest() }
    }

    /// Partial results keep coming while the user talks. When they stop for `silenceSeconds`, end the audio so
    /// the recognizer delivers its final result, then listening continues with a fresh request.
    private func armSilenceTimer(token current: Int) {
        let item = DispatchWorkItem { [weak self] in self?.closeUtterance(token: current) }
        lock.lock()
        silenceTimer?.cancel()
        silenceTimer = item
        lock.unlock()
        queue.asyncAfter(deadline: .now() + Self.silenceSeconds, execute: item)
    }

    private func closeUtterance(token current: Int) {
        lock.lock()
        guard isActive, current == token, let closing = request else {
            lock.unlock()
            return
        }
        request = nil  // later audio is dropped until the next request begins
        lock.unlock()

        closing.endAudio()
        queue.asyncAfter(deadline: .now() + Self.finalizeTimeoutSeconds) { [weak self] in
            self?.recoverIfStuck(token: current)
        }
    }

    /// The final result never arrived: start over rather than listening to nothing for the rest of the call.
    /// What was heard so far is delivered as a finished utterance first, so it is not lost.
    private func recoverIfStuck(token current: Int) {
        lock.lock()
        let isStuck = isActive && current == token
        let salvaged = lastPartial
        lock.unlock()
        guard isStuck else { return }
        retireRequest()
        if !salvaged.isEmpty { emit(.finished(salvaged)) }
        beginRequest()
    }

    private func emit(_ event: Event) {
        let deliver = onEvent
        DispatchQueue.main.async { deliver(event) }
    }
}

/// On-device listening for the whole call. Wraps the audio tap and `SFSpeechRecognizer`.
///
/// It does not touch the audio session: `SpeechOut.prepareAudioSession()` sets `.playAndRecord` / `.voiceChat`
/// once per call, so spoken replies and listening share one echo-cancelled route.
@MainActor
@Observable
final class SpeechIn {
    /// The words heard so far in the current utterance. Empty between utterances.
    private(set) var interim = ""
    /// True while the user has switched the mic off. The tap is stopped, not just ignored.
    private(set) var isMuted = false
    /// Recent mic loudness, oldest first, 0...1, at most `levelHistoryCount` values. Drives the waveform.
    private(set) var levels: [Double] = []
    static let levelHistoryCount = 28
    private(set) var isListening = false
    /// Whether this iPhone can recognise speech without the network. When false, use typed input.
    let onDeviceSupported: Bool

    @ObservationIgnored private let recognizer: SFSpeechRecognizer?
    @ObservationIgnored private var pipeline: RecognitionPipeline?
    @ObservationIgnored private var onResult: ((String, Bool) -> Void)?
    @ObservationIgnored private var onFailure: (() -> Void)?

    init(locale: Locale = .current) {
        let recognizer = Self.makeRecognizer(for: locale)
        self.recognizer = recognizer
        self.onDeviceSupported = recognizer?.supportsOnDeviceRecognition ?? false
    }

    /// Asks for the microphone, then speech recognition. Either can already have been answered; the system only
    /// prompts the first time. Neither call needs the main actor, so this is `nonisolated`.
    nonisolated static func authorize() async -> (mic: Bool, speech: Bool) {
        let mic = await AVAudioApplication.requestRecordPermission()
        let speech = await requestSpeechAuthorization()
        return (mic, speech)
    }

    /// Starts listening. `onResult` gets every partial result (`isFinal == false`) and each finished utterance
    /// (`isFinal == true`), on the main actor. Returns false if listening could not start (the caller then
    /// uses typed input). `onFailure` runs once, on the main actor, if the recognizer keeps failing after a
    /// successful start; listening has already been stopped by then.
    @discardableResult
    func start(onResult: @escaping (_ text: String, _ isFinal: Bool) -> Void,
               onFailure: (() -> Void)? = nil) -> Bool {
        stop()
        guard let recognizer, onDeviceSupported else { return false }
        let pipeline = RecognitionPipeline(recognizer: recognizer) { [weak self] event in
            // Delivered on the main queue by the pipeline.
            MainActor.assumeIsolated { self?.receive(event) }
        }
        do {
            try pipeline.start()
        } catch {
            return false
        }
        self.pipeline = pipeline
        self.onResult = onResult
        self.onFailure = onFailure
        isListening = true
        return true
    }

    func stop() {
        pipeline?.stop()
        pipeline = nil
        onResult = nil
        onFailure = nil
        interim = ""
        levels = []
        isListening = false
        isMuted = false
    }

    /// Mute stops the audio tap; unmute starts it again with a fresh request.
    func setMuted(_ muted: Bool) {
        guard isListening, muted != isMuted else { return }
        if muted {
            pipeline?.stop()
            interim = ""
            levels = []
            isMuted = true
        } else {
            do {
                try pipeline?.start()
                isMuted = false
            } catch {
                isMuted = true  // could not reopen the mic; stay muted
            }
        }
    }

    private func receive(_ event: RecognitionPipeline.Event) {
        guard isListening, !isMuted else { return }
        switch event {
        case .partial(let text):
            interim = text
            onResult?(text, false)
        case .finished(let text):
            interim = ""
            onResult?(text, true)
        case .failed:
            interim = ""
        case .gaveUp:
            let notify = onFailure
            stop()
            notify?()
        case .level(let rms):
            let level = smoothedLevel(previous: levels.last ?? 0, next: normalizedLevel(rms: rms))
            levels = appendingLevel(levels, level, capacity: Self.levelHistoryCount)
        }
    }

    /// The device locale if it can recognise on-device, else US English, else nothing.
    private static func makeRecognizer(for locale: Locale) -> SFSpeechRecognizer? {
        let candidates = [locale, Locale(identifier: "en-US")]
        for candidate in candidates {
            if let recognizer = SFSpeechRecognizer(locale: candidate), recognizer.supportsOnDeviceRecognition {
                return recognizer
            }
        }
        return nil
    }

    private nonisolated static func requestSpeechAuthorization() async -> Bool {
        await withCheckedContinuation { continuation in
            SFSpeechRecognizer.requestAuthorization { status in
                continuation.resume(returning: status == .authorized)
            }
        }
    }
}
