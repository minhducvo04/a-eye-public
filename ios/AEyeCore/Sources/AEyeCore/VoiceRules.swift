import Foundation

public enum InputMode: Equatable {
    case voice, typed
}

/// Voice input needs the mic, speech permission and on-device recognition; otherwise fall back to typing.
public func inputMode(micGranted: Bool, speechGranted: Bool, onDeviceSupported: Bool) -> InputMode {
    micGranted && speechGranted && onDeviceSupported ? .voice : .typed
}

/// True when `transcript` is the user talking over the app, not the app's own voice picked up by the mic.
/// - nothing is being spoken, or the transcript is empty: false
/// - every heard word already appears in the spoken line's word set (echo, `isEcho` in `client-web/voice.js`): false
/// - otherwise a final result, or an interim one with at least two words: true
public func isBargeIn(transcript: String, isFinal: Bool, speakingText: String?) -> Bool {
    guard let speakingText else { return false }
    let heard = words(of: transcript)
    guard !heard.isEmpty else { return false }
    let spoken = Set(words(of: speakingText))
    if heard.allSatisfy({ spoken.contains($0) }) { return false }
    return isFinal || heard.count >= 2
}

/// Same tokeniser as `wordsOf` in `client-web/voice.js`: lowercase, then runs of `[a-z0-9']`
/// (`CPU_FAN` -> `cpu`, `fan`; `don't` stays one word).
private func words(of text: String) -> [String] {
    var found: [String] = []
    var current = ""
    for scalar in text.lowercased().unicodeScalars {
        let isWordScalar = (scalar >= "a" && scalar <= "z") || (scalar >= "0" && scalar <= "9") || scalar == "'"
        if isWordScalar {
            current.unicodeScalars.append(scalar)
        } else if !current.isEmpty {
            found.append(current)
            current = ""
        }
    }
    if !current.isEmpty { found.append(current) }
    return found
}
