"""Tiny speech tier: Whisper tiny/base (faster-whisper), Moonshine tiny/base (transformers) and Piper TTS.

Runs on the GPU box in WSL (or a Pi). CPU rows use 4 threads to approximate a Raspberry Pi 5. Audio: the
LibriSpeech dummy set from Hugging Face (hf-internal-testing/librispeech_asr_dummy, 73 clips, LibriSpeech is
CC BY 4.0); TTS text: the 20 reference lines of eval/brain/cases.json. Writes one JSON line per row.

  python eval/tiers/speech.py --cases eval/brain/cases.json --out speech.jsonl
"""
import argparse
import json
import os
import re
import resource
import time
import wave
from pathlib import Path


def norm(text):
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9' ]", " ", text.lower())).strip()


def peak_mb():
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024


def librispeech():
    from datasets import load_dataset
    ds = load_dataset("hf-internal-testing/librispeech_asr_dummy", "clean", split="validation")
    return [(row["audio"]["array"].astype("float32"), row["audio"]["sampling_rate"], row["text"]) for row in ds]


def wer(refs, hyps):
    import jiwer
    return jiwer.wer([norm(r) for r in refs], [norm(h) for h in hyps])


def run_whisper(size, device, clips, threads=4):
    from faster_whisper import WhisperModel
    t0 = time.perf_counter()
    model = WhisperModel(size, device=device, compute_type="float16" if device == "cuda" else "int8",
                         cpu_threads=threads)
    load = time.perf_counter() - t0
    hyps, audio_s, t0 = [], 0.0, time.perf_counter()
    for audio, sr, _ in clips:
        segments, _ = model.transcribe(audio, language="en", beam_size=1, vad_filter=False)
        hyps.append(" ".join(s.text for s in segments))
        audio_s += len(audio) / sr
    return hyps, time.perf_counter() - t0, audio_s, load, model


def run_moonshine(name, device, clips, threads=4):
    import torch
    from transformers import AutoProcessor, MoonshineForConditionalGeneration
    torch.set_num_threads(threads)
    t0 = time.perf_counter()
    processor = AutoProcessor.from_pretrained(name)
    model = MoonshineForConditionalGeneration.from_pretrained(name).to(device)
    load = time.perf_counter() - t0
    hyps, audio_s, t0 = [], 0.0, time.perf_counter()
    for audio, sr, _ in clips:
        inputs = processor(audio, sampling_rate=sr, return_tensors="pt").to(device)
        limit = int(len(audio) / sr * 6.5) + 10  # the model card's tokens-per-second guard against loops
        with torch.inference_mode():
            ids = model.generate(**inputs, max_length=limit)
        hyps.append(processor.decode(ids[0], skip_special_tokens=True))
        audio_s += len(audio) / sr
    return hyps, time.perf_counter() - t0, audio_s, load, model


def run_piper(voice_dir, lines, out_dir):
    from piper import PiperVoice
    onnx = next(Path(voice_dir).glob("*.onnx"))
    t0 = time.perf_counter()
    voice = PiperVoice.load(str(onnx))
    load = time.perf_counter() - t0
    paths, audio_s, t0 = [], 0.0, time.perf_counter()
    for i, line in enumerate(lines):
        path = Path(out_dir) / f"piper_{i}.wav"
        with wave.open(str(path), "wb") as wav:
            voice.synthesize_wav(line, wav)
        with wave.open(str(path)) as wav:
            audio_s += wav.getnframes() / wav.getframerate()
        paths.append(path)
    return paths, time.perf_counter() - t0, audio_s, load, onnx.name


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--voice-dir", required=True)
    ap.add_argument("--only", default="", help='one job: "whisper tiny.en cpu", "moonshine moonshine-ai/moonshine-tiny cuda" or "piper"')
    a = ap.parse_args()
    out = open(a.out, "a")

    def emit(row):
        row["at"] = time.strftime("%H:%M:%S")
        out.write(json.dumps(row) + "\n")
        out.flush()
        print(json.dumps(row), flush=True)

    import torch
    gpu = torch.cuda.is_available()
    clips = librispeech()
    refs = [c[2] for c in clips]
    jobs = []
    for size in ("tiny.en", "base.en"):
        jobs += [("whisper", size, "cpu")] + ([("whisper", size, "cuda")] if gpu else [])
    for name in ("moonshine-ai/moonshine-tiny", "moonshine-ai/moonshine-base"):
        jobs += [("moonshine", name, "cpu")] + ([("moonshine", name, "cuda")] if gpu else [])
    for kind, name, device in jobs:
        if a.only and a.only != f"{kind} {name} {device}":
            continue  # one job per process, so peak RSS belongs to one model
        try:
            fn = run_whisper if kind == "whisper" else run_moonshine
            hyps, secs, audio_s, load, _ = fn(name, device, clips)
            emit(dict(kind="stt", model=name, device=device, threads=4 if device == "cpu" else None,
                      wer=round(wer(refs, hyps), 4), rtf=round(secs / audio_s, 4), audio_s=round(audio_s, 1),
                      load_s=round(load, 2), peak_rss_mb=round(peak_mb()), clips=len(clips)))
        except Exception as exc:
            emit(dict(kind="stt", model=name, device=device, error=type(exc).__name__ + ": " + str(exc)[:200]))
    if not a.only or a.only == "piper":
        os.environ["OMP_NUM_THREADS"] = "4"
        lines = [c["reference"] for c in json.load(open(a.cases))["talker"]]
        tmp = Path(a.out).with_suffix("")
        tmp.mkdir(exist_ok=True)
        try:
            paths, secs, audio_s, load, voice = run_piper(a.voice_dir, lines, tmp)
            from faster_whisper import WhisperModel
            judge = WhisperModel("base.en", device="cuda" if gpu else "cpu",
                                 compute_type="float16" if gpu else "int8")
            hyps = [" ".join(s.text for s in judge.transcribe(str(p), language="en", beam_size=1)[0]) for p in paths]
            emit(dict(kind="tts", model="piper " + voice, device="cpu", threads=4, rtf=round(secs / audio_s, 4),
                      audio_s=round(audio_s, 1), load_s=round(load, 2), peak_rss_mb=round(peak_mb()),
                      roundtrip_wer_whisper_base=round(wer(lines, hyps), 4), lines=len(lines)))
        except Exception as exc:
            emit(dict(kind="tts", model="piper", error=type(exc).__name__ + ": " + str(exc)[:200]))


if __name__ == "__main__":
    main()
