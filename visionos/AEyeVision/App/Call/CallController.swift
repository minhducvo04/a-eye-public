import AEyeCore
import Foundation
import Observation

/// Runs one call: owns the socket, the frame timer, spoken replies and the `CallState` the views render.
/// Copied from `ios/AEye/App/Call/CallController.swift`. Differences: frames come from the picked photo
/// (`PhotoSource`, see `visionos/PLAN.md`), and an optional server voice (`voiceId`).
/// All protocol decisions live in `AEyeCore`; this class only moves events between the phone and `CallState.reduce`.
/// Nothing here writes frames, audio or text to disk or to a log.
@MainActor
@Observable
final class CallController {
    private static let serverURLKey = "serverURL"
    private static let voiceKey = "voice"
    private static let privateKey = "private"

    private(set) var state = CallState.initial
    /// Non-nil while a call is active (including while reconnecting).
    private(set) var mode: Mode?
    private(set) var callStartedAt: Date?
    /// Pixel size of the last frame sent. The overlay maps 0..1 draw coordinates through this.
    private(set) var lastFrameSize: CGSize = .zero

    private(set) var pairing = PairingState()
    private(set) var pairingSocketURL: URL?

    private var storedServerURL: String

    /// Full server URL as typed in Settings. Saved to `UserDefaults`; an empty string is a real choice
    /// ("no server"), so the build default only applies before the user has ever saved one.
    var serverURLString: String {
        get { storedServerURL }
        set {
            storedServerURL = newValue
            UserDefaults.standard.set(newValue, forKey: Self.serverURLKey)
        }
    }

    /// Server voice id from `GET /api/voices`, or empty for the system voice (the iPhone default). Saved to
    /// `UserDefaults`; read when a call starts.
    var voiceId: String {
        get { storedVoiceId }
        set {
            storedVoiceId = newValue
            UserDefaults.standard.set(newValue, forKey: Self.voiceKey)
        }
    }
    private var storedVoiceId: String
    /// The voice this call was started with. A change in Settings applies to the next call.
    @ObservationIgnored private var callVoiceId = ""

    /// Private mode (`hello.private`): the server keeps the call on our own machines. Saved; applies to the
    /// next call. Forces the server voice and keeps Rewind off.
    var privateMode: Bool {
        get { storedPrivate }
        set {
            storedPrivate = newValue
            UserDefaults.standard.set(newValue, forKey: Self.privateKey)
            if newValue, rewind.on { setRewind(false) }
        }
    }
    private var storedPrivate: Bool
    /// Private mode as this call's `hello` sent it.
    private(set) var callPrivate = false

    // MARK: Rewind, expert and memory (see `CallController+Extras.swift`)

    /// One state behind the bottom-bar Rewind button and the Settings switch, like the web page's `rewind.js`.
    var rewind = RewindStatus(on: false, clips: 0, locked: false)
    /// Keep this call's TwelveLabs videos after the call (`rewind.keep`). Off by default.
    var rewindKeep = false
    /// The TwelveLabs upload notice, shown on the first switch-on of this app run.
    var showsRewindNotice = false
    @ObservationIgnored var rewindNoticed = false
    @ObservationIgnored var rewindNoticeTask: Task<Void, Never>?
    /// Rewind was on at some point in this call: the end of the call opens the Recap sheet.
    @ObservationIgnored var rewindUsedThisCall = false
    /// Set while the expert model works on a turn (`turn.stage == expert`); cleared by its own `expert_done`.
    var expert: (turnId: String, startedAt: Date, detail: String?)?
    /// The Rewind answer: the earlier frame boxed, beside the live view.
    var rewindHit: RewindHit?
    /// The Recap sheet. Lives here (not in the call screen) because it also opens after the call ends.
    var recap: Recap?
    /// Newest `track` boxes on the live picture, for the split view's arrow.
    var liveObjects: [SceneBox] = []
    /// What is remembered today (memory status, or this call's Rewind clips), and a short result line.
    var memoryCount = 0
    var memoryNotice: String?
    @ObservationIgnored var memoryTask: Task<Void, Never>?
    /// Server voice the call asked for heard nothing (no voice engine on the server): speak locally instead.
    @ObservationIgnored var serverVoiceSilent = false
    @ObservationIgnored var heardServerAudio = false
    /// The headset mic was on when a phone joined as camera and mic; turn it back on when the phone leaves.
    @ObservationIgnored var resumeMicAfterPhone = false

    /// Spoken replies. Exposed so voice input can read `speakingText` / `startedAt` and call `stop()`.
    let speech = SpeechOut()

    /// On-device listening. Exposed so the call screen can show the interim transcript and the mute state.
    let listener = SpeechIn()
    /// How the user talks to the agent. Typed until permissions are resolved (see `isInputResolved`).
    private(set) var inputMode: InputMode = .typed
    /// False between the start of a call and the moment mic and speech permissions are answered, so the typed
    /// field does not flash up and vanish while the system prompts are showing.
    private(set) var isInputResolved = false

    private let camera: PhotoSource
    @ObservationIgnored private var socket: SocketClient?
    @ObservationIgnored private var generation = 0
    @ObservationIgnored private var isSocketOpen = false
    @ObservationIgnored private var isFrameInFlight = false
    @ObservationIgnored private(set) var sessionId = ""
    /// Continues across calls for the whole app run; never reset.
    @ObservationIgnored private var seq = 0
    @ObservationIgnored private var frameTask: Task<Void, Never>?
    @ObservationIgnored private var reconnectTask: Task<Void, Never>?
    /// Bumped when a call starts or ends, so a permission prompt answered late cannot start listening for a
    /// call that is already over.
    @ObservationIgnored private var listenToken = 0
    /// True from the first partial result of an utterance until its final result.
    @ObservationIgnored private var isUtteranceInProgress = false
    /// The spoken line the utterance may be an echo of, decided when its first partial arrived: the line being
    /// spoken then, or one that ended less than `echoWindowSeconds` before. nil = cannot be an echo.
    @ObservationIgnored private var utteranceEchoReference: String?
    private static let echoWindowSeconds: TimeInterval = 1.0

    init(camera: PhotoSource) {
        self.camera = camera
        self.storedServerURL = UserDefaults.standard.string(forKey: Self.serverURLKey) ?? Self.bundledServerURL()
        self.storedVoiceId = UserDefaults.standard.string(forKey: Self.voiceKey) ?? ""
        self.storedPrivate = UserDefaults.standard.bool(forKey: Self.privateKey)
    }

    /// Milliseconds since 1970. The clock `CallState.reduce(nowMs:)` and `DrawState.visible(nowMs:)` share.
    static func nowMs() -> Double {
        Date().timeIntervalSince1970 * 1000
    }

    // MARK: - Call lifecycle

    func start(_ mode: Mode) {
        guard self.mode == nil, mode.isAvailable, serverURL(from: serverURLString) != nil else { return }
        self.mode = mode
        state = .initial
        sessionId = UUID().uuidString
        callStartedAt = Date()
        callVoiceId = voiceId
        callPrivate = privateMode
        resetExtras()
        speech.resetServerVoice()
        speech.prepareAudioSession()
        connect()
        startFrameLoop()
        beginListening()
    }

    func end() {
        listenToken += 1
        listener.stop()
        resetUtterance()
        inputMode = .typed
        isInputResolved = false
        frameTask?.cancel()
        frameTask = nil
        reconnectTask?.cancel()
        reconnectTask = nil
        generation += 1  // late callbacks from the old socket are ignored
        socket?.close()
        socket = nil
        isSocketOpen = false
        isFrameInFlight = false
        speech.stop()
        speech.releaseAudioSession()
        state = .initial
        pairing = PairingState()
        let endedSession = sessionId
        let endedURL = pairingSocketURL
        pairingSocketURL = nil
        let showRecap = rewindUsedThisCall && !callPrivate
        resetExtras()
        mode = nil
        callStartedAt = nil
        lastFrameSize = .zero
        // End-of-call recap when Rewind was on, as on the web page (the server uploads the last clips first).
        if showRecap, let endedURL { openRecap(sessionId: endedSession, socketURL: endedURL) }
    }

    /// Whether this call's replies come as server `audio` (a picked server voice, or Private mode).
    var usesServerVoice: Bool {
        (!callVoiceId.isEmpty || callPrivate) && !serverVoiceSilent
    }

    // MARK: - Things the views ask for

    func send(text: String) {
        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }
        guard isSocketOpen, let socket else {
            state.errorText = "Not connected."
            return
        }
        socket.send(ClientMessage.userText(sessionId: sessionId, text: clean)) { _ in }
    }

    func confirmPowerOff() {
        guard isSocketOpen, let socket else { return }
        socket.send(ClientMessage.powerOff(sessionId: sessionId)) { _ in }
        apply(.powerOffTapped)
    }

    func requestPairing() {
        guard isSocketOpen, let socket, !pairing.joined else { return }
        pairing = PairingState()
        pairing.status = "Requesting pairing code…"
        let sentGeneration = generation
        socket.send(PairingMessage.request(sessionId: sessionId)) { [weak self] sent in
            guard let self, sentGeneration == self.generation, !sent else { return }
            self.pairing.status = "Could not request a code. Try again."
        }
    }

    /// Sends one message on the open socket. False when there is none.
    @discardableResult
    func sendRaw(_ data: Data) -> Bool {
        guard isSocketOpen, let socket else { return false }
        socket.send(data) { _ in }
        return true
    }

    func dismissMarket() {
        apply(.marketDismissed)
    }

    /// Mic button: stops or restarts the audio tap. Does nothing in typed mode.
    func toggleMute() {
        guard inputMode == .voice else { return }
        listener.setMuted(!listener.isMuted)
        resetUtterance()  // muting drops any half-heard utterance
    }

    // MARK: - Listening

    /// Asks for mic and speech permission, then starts on-device listening if everything allows it.
    /// Otherwise the call carries on with the typed field.
    private func beginListening() {
        listenToken += 1
        let token = listenToken
        inputMode = .typed
        isInputResolved = false
        #if DEBUG
        // Simulator runs: typed input without asking for the mic (see `DebugHooks`).
        if UserDefaults.standard.bool(forKey: "AEyeDebugTyped") {
            // -AEyeDebugVoiceUI YES: lay out the voice controls (waveform strip) without opening the mic.
            if UserDefaults.standard.bool(forKey: "AEyeDebugVoiceUI") { inputMode = .voice }
            isInputResolved = true
            return
        }
        #endif
        Task { [weak self] in
            let granted = await SpeechIn.authorize()
            guard let self, token == self.listenToken, self.mode != nil else { return }
            // Qualified: the property `inputMode` would otherwise hide AEyeCore's function of the same name.
            let resolved = AEyeCore.inputMode(
                micGranted: granted.mic,
                speechGranted: granted.speech,
                onDeviceSupported: self.listener.onDeviceSupported
            )
            inputMode = resolved
            if resolved == .voice {
                let didStart = listener.start(
                    onResult: { [weak self] text, isFinal in
                        self?.heard(text, isFinal: isFinal)
                    },
                    onFailure: { [weak self] in
                        self?.listeningFailed()
                    }
                )
                if !didStart { inputMode = .typed }
            }
            isInputResolved = true
        }
    }

    /// The recogniser kept failing and `SpeechIn` has stopped itself. Carry on with the typed field.
    private func listeningFailed() {
        guard mode != nil else { return }
        resetUtterance()
        inputMode = .typed
        isInputResolved = true
    }

    /// A partial or final transcript. Runs on the main actor.
    private func heard(_ text: String, isFinal: Bool) {
        guard mode != nil else { return }

        // The user talking over the app: stop the voice. Echo of the app's own voice is not a barge-in.
        if isBargeIn(transcript: text, isFinal: isFinal, speakingText: speech.speakingText) {
            interruptSpeech()
        }

        // Note, once per utterance and at its first words, whether it began while the app was speaking or just
        // after: only then can it be the app's own voice. Asking at the end would also catch a genuine reply.
        if !isUtteranceInProgress, !text.isEmpty {
            isUtteranceInProgress = true
            utteranceEchoReference = speech.echoReferenceText(window: Self.echoWindowSeconds)
        }

        guard isFinal else { return }
        let reference = isUtteranceInProgress
            ? utteranceEchoReference
            : speech.echoReferenceText(window: Self.echoWindowSeconds)
        resetUtterance()

        let clean = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !clean.isEmpty else { return }

        // Began during or just after the app's line, and is part of that line: the mic heard the speaker.
        if let reference, !isBargeIn(transcript: clean, isFinal: true, speakingText: reference) {
            return
        }
        send(text: clean)
    }

    private func resetUtterance() {
        isUtteranceInProgress = false
        utteranceEchoReference = nil
    }

    private func interruptSpeech() {
        let utteranceId = speech.speakingUtteranceId
        let atMs = max(0, Int((speech.startedAt.map { Date().timeIntervalSince($0) } ?? 0) * 1000))
        speech.stop()
        guard let utteranceId, isSocketOpen, let socket else { return }
        socket.send(ClientMessage.bargeIn(sessionId: sessionId, utteranceId: utteranceId, atMs: atMs)) { _ in }
    }

    // MARK: - Socket

    private func connect() {
        guard let mode, mode.pack != nil, let url = serverURL(from: serverURLString) else { return }
        pairing = PairingState()
        pairingSocketURL = url
        generation += 1
        let thisGeneration = generation
        socket?.close()
        isSocketOpen = false
        isFrameInFlight = false
        socket = SocketClient(
            url: url,
            onOpen: { [weak self] in self?.socketDidOpen(thisGeneration) },
            onMessage: { [weak self] data in self?.socketDidReceive(data, thisGeneration) },
            onClose: { [weak self] in self?.socketDidClose(thisGeneration) }
        )
    }

    private func socketDidOpen(_ socketGeneration: Int) {
        guard socketGeneration == generation, let pack = mode?.pack, let socket else { return }
        isSocketOpen = true
        apply(.connected)
        // Private mode never sends a Cartesia voice id: the server would only fall back to Kokoro anyway.
        let voice = callPrivate && callVoiceId.hasPrefix("cartesia:") ? nil : callVoiceId
        let hello = Wire.hello(sessionId: sessionId, pack: pack, serverVoice: !callVoiceId.isEmpty || callPrivate,
                               voice: voice, rewind: rewind.on && !callPrivate, isPrivate: callPrivate)
        socket.send(hello) { _ in }
        if rewind.on, !callPrivate {
            if rewindKeep { sendRewind() }
            startMemoryPolling()
        }
    }

    private func socketDidReceive(_ data: Data, _ socketGeneration: Int) {
        guard socketGeneration == generation else { return }
        if let message = PairingMessage.decode(data) {
            let wasJoined = pairing.joined
            pairing.receive(message)
            if wasJoined != pairing.joined {
                state.draw = .empty  // Frame sequence numbers belong to different camera sources.
                phoneMicChanged(joined: pairing.joined)
            }
            return
        }
        if let extra = ExtraMessage.decode(data) {
            receive(extra)
            return
        }
        guard let message = ServerMessage.decode(data) else { return }
        if case .ignored(type: "audio") = message {
            if usesServerVoice, let chunk = ServerVoiceMessage.audio(data) {
                heardServerAudio = true
                speech.playServerAudio(chunk)
            }
            return
        }
        apply(.server(message))
        // A cancelled turn (a new question, or the paired phone talking over the answer) stops its voice here too.
        if case .turn(_, .cancelled, _, _) = message { speech.stop() }
        if case .say(_, let utteranceId, let text) = message, !text.isEmpty {
            if usesServerVoice {
                speech.queueServerLine(text: text, utteranceId: utteranceId)
                watchServerVoice(text: text, utteranceId: utteranceId)
            } else {
                speech.speak(text: text, utteranceId: utteranceId)
            }
        }
    }

    private func socketDidClose(_ socketGeneration: Int) {
        guard socketGeneration == generation, mode != nil else { return }
        socket = nil
        isSocketOpen = false
        isFrameInFlight = false
        pairing = PairingState()
        apply(.disconnected)
        scheduleReconnect()
    }

    private func scheduleReconnect() {
        reconnectTask?.cancel()
        reconnectTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: UInt64(Tuning.reconnectDelay * 1_000_000_000))
            guard !Task.isCancelled else { return }
            self?.connect()
        }
    }

    // MARK: - Frames

    private func startFrameLoop() {
        frameTask?.cancel()
        frameTask = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds: UInt64(Tuning.frameInterval * 1_000_000_000))
                guard !Task.isCancelled else { return }
                self?.sendFrameIfReady()
            }
        }
    }

    private func sendFrameIfReady() {
        guard !pairing.joined, isSocketOpen, !isFrameInFlight, let socket, let jpeg = camera.latestJPEG() else { return }
        seq += 1
        let message = ClientMessage.frame(
            sessionId: sessionId,
            seq: seq,
            tsMs: Int64(Date().timeIntervalSince1970 * 1000),
            width: jpeg.width,
            height: jpeg.height,
            jpeg: jpeg.data
        )
        let size = CGSize(width: jpeg.width, height: jpeg.height)
        if size != lastFrameSize { lastFrameSize = size }

        isFrameInFlight = true
        let sentGeneration = generation
        socket.send(message) { [weak self] _ in
            guard let self, sentGeneration == self.generation else { return }
            self.isFrameInFlight = false
        }
    }

    #if DEBUG
    /// Debug builds only: feeds one server message as if it came on the socket (`-AEyeDebugInject`).
    func debugReceive(_ data: Data) {
        socketDidReceive(data, generation)
    }
    #endif

    // MARK: - State

    private func apply(_ event: CallEvent) {
        state = state.reduce(event, nowMs: Self.nowMs())
    }

    /// `AEYE_SERVER_URL` from Info.plist. An unresolved `$(AEYE_SERVER_URL)` (xcconfig missing) counts as empty.
    private static func bundledServerURL() -> String {
        let raw = Bundle.main.object(forInfoDictionaryKey: "AEYE_SERVER_URL") as? String ?? ""
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.hasPrefix("$(") ? "" : trimmed
    }
}
