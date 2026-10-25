import AVFoundation

/// Bridges `AVSpeechSynthesizer` callbacks (which are not main-actor annotated) onto the main actor.
/// `nonisolated` on purpose, so it stays non-isolated when the project's Default Actor Isolation is MainActor.
private nonisolated final class SynthesizerDelegateProxy: NSObject, AVSpeechSynthesizerDelegate, @unchecked Sendable {
    var onEnd: ((AVSpeechUtterance) -> Void)?

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didFinish utterance: AVSpeechUtterance) {
        notify(utterance)
    }

    func speechSynthesizer(_ synthesizer: AVSpeechSynthesizer, didCancel utterance: AVSpeechUtterance) {
        notify(utterance)
    }

    private func notify(_ utterance: AVSpeechUtterance) {
        DispatchQueue.main.async { [self] in onEnd?(utterance) }
    }
}

/// Speaks each `say` line with the system voice (the app sends `tts:"browser"`). A new line cuts off the old one.
@MainActor
final class SpeechOut: NSObject {
    /// Set while a line is being spoken; nil otherwise.
    private(set) var speakingUtteranceId: String?
    private(set) var speakingText: String?
    private(set) var startedAt: Date?

    /// The line most recently finished or cut off, kept briefly after speech ends. The mic can still be
    /// transcribing the tail of that line a moment later, and it must not be taken for the user talking.
    private var lastSpokenText: String?
    private var lastSpokenEndedAt: Date?

    private let synthesizer = AVSpeechSynthesizer()
    private let delegateProxy = SynthesizerDelegateProxy()
    private var currentUtterance: AVSpeechUtterance?

    override init() {
        super.init()
        synthesizer.delegate = delegateProxy
        delegateProxy.onEnd = { [weak self] utterance in
            MainActor.assumeIsolated { self?.didEnd(utterance) }
        }
    }

    func speak(text: String, utteranceId: String) {
        stop()
        let utterance = AVSpeechUtterance(string: text)
        currentUtterance = utterance
        speakingUtteranceId = utteranceId
        speakingText = text
        startedAt = Date()
        synthesizer.speak(utterance)
    }

    func stop() {
        rememberSpokenLine()
        currentUtterance = nil
        speakingUtteranceId = nil
        speakingText = nil
        startedAt = nil
        if synthesizer.isSpeaking {
            synthesizer.stopSpeaking(at: .immediate)
        }
    }

    /// The text the mic may be hearing as an echo: the line being spoken, or the one that ended within
    /// `window` seconds (just long enough for the tail of the line to reach the recogniser). nil when there is
    /// nothing the mic could be echoing. The caller asks at the moment an utterance starts, not when it ends.
    func echoReferenceText(window: TimeInterval = 1.0) -> String? {
        if let speakingText { return speakingText }
        guard let lastSpokenText, let lastSpokenEndedAt,
              Date().timeIntervalSince(lastSpokenEndedAt) <= window else { return nil }
        return lastSpokenText
    }

    /// One audio session for the whole call, shared by spoken replies and on-device listening:
    /// `.playAndRecord` + `.voiceChat` selects the voice-chat route (`SpeechIn` also turns on voice processing on
    /// its input node, which is what actually cancels echo), `.defaultToSpeaker` keeps replies on
    /// the loudspeaker (voiceChat alone would use the earpiece). Configured once here, before either side
    /// starts, so `SpeechIn` never changes the category. If setting it fails the default category still
    /// speaks, so the error is not fatal.
    func prepareAudioSession() {
        let audio = AVAudioSession.sharedInstance()
        try? audio.setCategory(.playAndRecord, mode: .voiceChat, options: [.defaultToSpeaker, .allowBluetooth])
        try? audio.setActive(true)
    }

    func releaseAudioSession() {
        try? AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
    }

    private func rememberSpokenLine() {
        guard let speakingText else { return }
        lastSpokenText = speakingText
        lastSpokenEndedAt = Date()
    }

    private func didEnd(_ utterance: AVSpeechUtterance) {
        // A cancel for an utterance we already replaced must not clear the new one.
        guard utterance === currentUtterance else { return }
        rememberSpokenLine()
        currentUtterance = nil
        speakingUtteranceId = nil
        speakingText = nil
        startedAt = nil
    }
}
