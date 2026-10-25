"""Kokoro voice pick: first-audio latency per voice from the box, plus a blind naturalness rating.

    ~/Projects/kyra/.venv/bin/python -m voice.voice_pick [out_dir]

The rating is a proxy listen test: each clip (the AI's own synthetic sentence, no user audio) goes to
Gemini under a shuffled label and gets a 1-5 human-likeness score. A person's listen test still wins.
"""
import base64
import json
import os
import random
import statistics
import sys
import time
import wave

import httpx

from voice.bench import load_env

VOICES = ['af_heart', 'af_bella', 'af_nicole', 'af_sarah', 'af_sky', 'af_aoede', 'af_kore',
          'am_michael', 'am_fenrir', 'am_puck', 'am_echo', 'bf_emma', 'bm_george', 'bm_fable']
LINE = "Okay, got it. That little four-pin plug goes on the CPU fan header, right up by the socket. See it?"
RATE = 16000


def synth(box, voice):
    started = time.monotonic()
    first = None
    pcm = b''
    with httpx.stream('POST', box + '/tts', json=dict(text=LINE, voice=voice), timeout=20) as response:
        if response.status_code != 200:
            return None, None
        for chunk in response.iter_bytes(2048):
            if chunk and first is None:
                first = (time.monotonic() - started) * 1000
            pcm += chunk
    return first, pcm


def rate(key, clips):
    labels = list(clips)
    random.Random(7).shuffle(labels)
    parts = [dict(text='Rate each clip for how much it sounds like a real person talking casually (not a robot): '
                       '1 = clearly synthetic, 5 = indistinguishable from a human. Judge prosody, rhythm, breath '
                       'and warmth, not the words. Reply only JSON: {"clip_1": n, ...}.')]
    order = {}
    for i, voice in enumerate(labels, 1):
        order[f'clip_{i}'] = voice
        parts.append(dict(text=f'clip_{i}:'))
        parts.append(dict(inline_data=dict(mime_type='audio/wav', data=base64.b64encode(clips[voice]).decode())))
    response = httpx.post('https://generativelanguage.googleapis.com/v1beta/models/gemini-3.8-flash:generateContent',
                          headers={'x-goog-api-key': key}, timeout=120,
                          json=dict(contents=[dict(role='user', parts=parts)],
                                    generationConfig=dict(responseMimeType='application/json')))
    if response.status_code != 200:
        response = httpx.post('https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent',
                              headers={'x-goog-api-key': key}, timeout=120,
                              json=dict(contents=[dict(role='user', parts=parts)],
                                        generationConfig=dict(responseMimeType='application/json')))
    response.raise_for_status()
    text = response.json()['candidates'][0]['content']['parts'][0]['text']
    scores = json.loads(text)
    return {order[k]: v for k, v in scores.items() if k in order}


def wav_bytes(pcm):
    import io
    out = io.BytesIO()
    with wave.open(out, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm)
    return out.getvalue()


def main(out_dir):
    if not os.environ.get("BOX_URL"):
        raise SystemExit("Set BOX_URL to your local service URL before running this script")
    load_env()
    box = os.environ.get('BOX_URL', 'http://127.0.0.1:8090').rstrip('/')
    os.makedirs(out_dir, exist_ok=True)
    clips, latency = {}, {}
    for voice in VOICES:
        firsts = []
        pcm = None
        for _ in range(3):
            first, pcm = synth(box, voice)
            if first is None:
                break
            firsts.append(first)
        if not firsts:
            print(f'{voice}: not on the box')
            continue
        latency[voice] = statistics.median(firsts)
        clips[voice] = wav_bytes(pcm)
        with open(os.path.join(out_dir, voice + '.wav'), 'wb') as f:
            f.write(clips[voice])
    runs = [rate(os.environ.get('GOOGLE_API_KEY', ''), clips) for _ in range(int(os.environ.get("RATE_RUNS", "2")))]
    for voice in sorted(clips, key=lambda v: -statistics.mean(r.get(v, 0) for r in runs)):
        print(f'{voice}: blind score {statistics.mean(r.get(voice, 0) for r in runs):.1f}/5, '
              f'first audio {latency[voice]:.0f} ms (median of 3)')


if __name__ == '__main__':
    main(sys.argv[1] if len(sys.argv) > 1 else 'voice-pick')
