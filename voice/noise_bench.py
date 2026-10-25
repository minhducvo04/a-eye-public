"""Noisy-hall STT bench: synthetic speech mixed with synthetic crowd babble at a set SNR, sent to box Whisper.

    .venv/bin/python -m voice.noise_bench [box_url]

Everything is synthetic (macOS `say`); no real recordings. Prints words caught per SNR and setting.
"""
import base64
import os
import re
import subprocess
import sys
import tempfile
import wave

import httpx
import numpy as np

RATE = 16000
TARGETS = ['Where does this fan cable go', 'Which slot is the RAM', 'Is that the CPU fan header',
           'What do I plug in next', 'Show me the front panel header']
BABBLE = ['The keynote starts at nine in the main hall', 'Has anyone seen the charger for this laptop',
          'We should get pizza before the judges come', 'My demo crashed again so I am rewriting it',
          'Free stickers at the sponsor booth over there', 'Can you push the latest build to the repo']
VOICES = ['Samantha', 'Daniel', 'Karen', 'Moira', 'Rishi', 'Tessa']


def say(text, voice=None):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, 'x.wav')
        cmd = ['say', '-o', path, '--data-format=LEI16@16000'] + (['-v', voice] if voice else []) + [text]
        if subprocess.run(cmd, capture_output=True).returncode:
            subprocess.run(['say', '-o', path, '--data-format=LEI16@16000', text], check=True)
        with wave.open(path) as source:
            return np.frombuffer(source.readframes(source.getnframes()), '<i2').astype(np.float32) / 32768


def crowd(seconds, seed):
    rng = np.random.default_rng(seed)
    out = np.zeros(int(RATE * seconds), np.float32)
    for i, line in enumerate(BABBLE):
        talk = say(line, VOICES[i % len(VOICES)])
        start = int(rng.integers(0, max(1, len(out) - len(talk) // 2)))
        piece = talk[: len(out) - start]
        out[start:start + len(piece)] += piece * rng.uniform(0.5, 1.0)
    pink = np.cumsum(rng.normal(0, 1, len(out))).astype(np.float32)
    pink -= np.convolve(pink, np.ones(400) / 400, mode='same')
    return out + 0.3 * pink / (np.abs(pink).max() + 1e-9) * np.abs(out).max()


def mix(speech, noise, snr_db, quiet=0.15):
    speech = speech / (np.sqrt(np.mean(speech ** 2)) + 1e-9) * quiet * 0.3  # soft, far from the mic
    pad = np.zeros(RATE // 2, np.float32)
    voice = np.concatenate([pad, speech, pad])
    noise = noise[: len(voice)]
    gain = np.sqrt(np.mean(voice[RATE // 2:-RATE // 2] ** 2) / (np.mean(noise ** 2) + 1e-12) / 10 ** (snr_db / 10))
    out = voice + noise * gain
    return np.clip(out, -1, 1)


def words(text):
    return re.findall(r"[a-z0-9']+", text.lower())


def caught(target, heard):
    want, got = words(target), set(words(heard))
    return sum(w in got for w in want), len(want)


def main(url):
    rows = {}
    for snr in (5, 0):
        for i, target in enumerate(TARGETS):
            noisy = mix(say(target), crowd(4, seed=i + snr), snr)
            pcm = (noisy * 32767).astype('<i2').tobytes()
            response = httpx.post(url.rstrip('/') + '/stt', timeout=20, json=dict(
                pcm16_b64=base64.b64encode(pcm).decode(), language='en'))
            heard = response.json().get('text', '') if response.status_code == 200 else f'<{response.status_code}>'
            hit, total = caught(target, heard)
            rows.setdefault(snr, []).append((hit, total))
            print(f'SNR {snr} dB | want: {target} | heard: {heard[:80]} | {hit}/{total}')
    for snr, items in rows.items():
        print(f'SNR {snr} dB: {sum(h for h, _ in items)}/{sum(t for _, t in items)} target words caught')


if __name__ == '__main__':
    if len(sys.argv) <= 1 and not os.environ.get('BOX_URL'):
        raise SystemExit('Set BOX_URL to your local service URL or pass a URL argument')
    main(sys.argv[1] if len(sys.argv) > 1 else os.environ.get('BOX_URL', 'http://127.0.0.1:8090'))
