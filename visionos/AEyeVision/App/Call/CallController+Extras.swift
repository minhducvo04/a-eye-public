import AEyeCore
import Foundation

/// Round-2 features on top of the iPhone call: Rewind (on/off, upload notice, Forget, Recap), the expert wait,
/// the memory-aid answer and count, the paired phone's mic, and a fallback when the server voice is silent.
/// Behaviour follows `client-web/rewind.js`, `turn.js`, `memory.js` and `pair.js`.
extension CallController {
    func resetExtras() {
        rewind.clips = 0
        rewind.locked = false
        rewindUsedThisCall = rewind.on && !privateMode
        expert = nil
        rewindHit = nil
        liveObjects = []
        memoryCount = 0
        memoryNotice = nil
        memoryTask?.cancel()
        memoryTask = nil
        serverVoiceSilent = false
        heardServerAudio = false
        resumeMicAfterPhone = false
    }

    func receive(_ message: ExtraMessage) {
        switch message {
        case .expert(let turnId, let detail):
            expert = (turnId, Date(), detail)
        case .expertDone(let turnId):
            if expert?.turnId == turnId { expert = nil }
        case .rewindState(let status):
            rewind = status
            if status.on { rewindUsedThisCall = true }
            if status.on { memoryCount = max(memoryCount, status.clips) }
            if status.locked, status.on == false { showsRewindNotice = false }
        case .rewindHit(let hit):
            rewindHit = hit
        case .rewindRecap(let recap):
            self.recap = recap
        case .track(let objects):
            liveObjects = objects
        }
    }

    // MARK: - Rewind

    /// Either control flipped Rewind. Never on in Private mode; the server also refuses when it is locked.
    func setRewind(_ on: Bool) {
        let next = on && !privateMode && !rewind.locked
        rewind.on = next
        if !next { rewind.clips = 0 }
        if next {
            rewindUsedThisCall = mode != nil
            startMemoryPolling()
        }
        if next, !rewindNoticed {
            rewindNoticed = true
            showsRewindNotice = true
            rewindNoticeTask?.cancel()
            rewindNoticeTask = Task { [weak self] in
                try? await Task.sleep(nanoseconds: 8_000_000_000)
                guard !Task.isCancelled else { return }
                self?.showsRewindNotice = false
            }
        }
        sendRewind()
    }

    func sendRewind() {
        guard mode != nil else { return }
        sendRaw(Wire.rewind(sessionId: sessionId, on: rewind.on, keep: rewindKeep))
    }

    func setRewindKeep(_ keep: Bool) {
        rewindKeep = keep
        if rewind.on { sendRewind() }
    }

    /// Recap during the call (the Recap chip) or after it (when Rewind was on). POST /api/rewind/recap.
    func openRecap(sessionId: String? = nil, socketURL: URL? = nil) {
        guard let url = socketURL ?? pairingSocketURL else { return }
        let id = sessionId ?? self.sessionId
        recap = Recap(pending: true)
        Task { [weak self] in
            let result = await ServerAPI.recap(sessionId: id, socketURL: url)
            guard let self, self.recap?.pending == true else { return }
            self.recap = result
        }
    }

    /// "Forget this call's clips" (Settings) and the memory aid's "Forget that" / "Forget today".
    func forget(scope: String? = nil) {
        guard let url = pairingSocketURL, mode != nil else {
            memoryNotice = "Nothing to forget."
            return
        }
        let id = sessionId
        Task { [weak self] in
            let deleted = await ServerAPI.forget(sessionId: id, scope: scope, socketURL: url)
            guard let self else { return }
            if let deleted {
                self.memoryNotice = "Forgot \(deleted) clip\(deleted == 1 ? "" : "s")."
                self.memoryCount = 0
                self.rewind.clips = 0
                self.rewind.on = false
                self.rewindHit = nil
            } else {
                self.memoryNotice = "Nothing to forget."
            }
            try? await Task.sleep(nanoseconds: 4_000_000_000)
            if self.memoryNotice != nil { self.memoryNotice = nil }
        }
    }

    /// Remembered count every 10 s while Rewind is on, like the web page.
    func startMemoryPolling() {
        guard memoryTask == nil, let url = pairingSocketURL else { return }
        let id = sessionId
        memoryTask = Task { [weak self] in
            while !Task.isCancelled {
                if let count = await ServerAPI.memoryCount(sessionId: id, socketURL: url) {
                    guard let self, !Task.isCancelled else { return }
                    self.memoryCount = count
                }
                try? await Task.sleep(nanoseconds: 10_000_000_000)
            }
        }
    }

    // MARK: - Paired phone mic

    /// A paired phone is the camera and the mic: the headset mic is off meanwhile (`pair.js` phoneMicRoute),
    /// and comes back when the phone leaves if it was on before.
    func phoneMicChanged(joined: Bool) {
        guard inputMode == .voice else { return }
        if joined, !listener.isMuted {
            resumeMicAfterPhone = true
            toggleMute()
        } else if !joined, resumeMicAfterPhone {
            resumeMicAfterPhone = false
            if listener.isMuted { toggleMute() }
        }
    }

    // MARK: - Server voice fallback

    /// The server falls back to text-only when it has no voice engine (no Kokoro box, no Cartesia key). If no
    /// `audio` has arrived 2 s after the first line, speak this call's lines with the headset's system voice.
    func watchServerVoice(text: String, utteranceId: String) {
        guard !heardServerAudio else { return }
        Task { [weak self] in
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            guard let self, self.mode != nil, !self.heardServerAudio, !self.serverVoiceSilent else { return }
            self.serverVoiceSilent = true
            self.speech.stop()
            self.speech.speak(text: text, utteranceId: utteranceId)
        }
    }
}
