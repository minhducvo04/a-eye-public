import Foundation

/// Microphone loudness for the waveform. Only a single number per audio buffer leaves the tap; no audio is kept.

/// Below this the room is treated as silent, at or above `levelCeilingDb` the bar is full.
private let levelFloorDb = -55.0
private let levelCeilingDb = -10.0
/// Share of the old level kept per buffer while the sound falls (about 20 ms per buffer).
private let levelRelease = 0.82
/// Normalised level that counts as the user talking rather than room noise.
private let voiceThreshold = 0.3

/// RMS amplitude (0...1) to 0...1 on a decibel scale, so quiet speech still moves the bars.
public func normalizedLevel(rms: Float) -> Double {
    guard rms.isFinite, rms > 0 else { return 0 }
    let db = 20 * log10(Double(rms))
    return min(1, max(0, (db - levelFloorDb) / (levelCeilingDb - levelFloorDb)))
}

/// Jumps up at once and falls off gently, so bars do not flicker between syllables.
public func smoothedLevel(previous: Double, next: Double) -> Double {
    next >= previous ? next : previous * levelRelease + next * (1 - levelRelease)
}

/// A new history with `level` at the end and only the newest `capacity` values. `history` is left unchanged.
public func appendingLevel(_ history: [Double], _ level: Double, capacity: Int) -> [Double] {
    Array((history + [level]).suffix(max(0, capacity)))
}

public func isVoiceLevel(_ level: Double) -> Bool {
    level >= voiceThreshold
}
