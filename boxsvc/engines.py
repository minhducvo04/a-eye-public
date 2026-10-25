"""Lazy model adapters. Request media stays in memory."""
import os
import struct
from threading import Lock


def pcm16_16khz(audio):
    """Linearly resample one mono 24 kHz segment to little-endian PCM16."""
    if hasattr(audio, "detach"):
        audio = audio.detach().cpu().numpy()
    count = len(audio) * 2 // 3
    samples = []
    for i in range(count):
        position = i * 1.5
        left = int(position)
        fraction = position - left
        value = float(audio[left]) * (1 - fraction)
        value += float(audio[min(left + 1, len(audio) - 1)]) * fraction
        samples.append(round(max(-1.0, min(1.0, value)) * 32767))
    return struct.pack(f"<{count}h", *samples)


class SttEngine:
    def __init__(self, device):
        import numpy as np
        from faster_whisper import WhisperModel

        self.np = np
        self.lock = Lock()
        self.model = WhisperModel(
            os.getenv("STT_MODEL", "large-v3-turbo"), device=device,
            compute_type="float16" if device == "cuda" else "int8",
        )

    def transcribe(self, pcm, language):
        audio = self.np.frombuffer(pcm, dtype="<i2").astype(self.np.float32) / 32768
        with self.lock:
            segments, _ = self.model.transcribe(audio, language=language, **NOISY_HALL)
            return "".join(segment.text for segment in segments).strip()


# Loud hackathon hall, soft speaker far from the mic (docs/voice-options.md, voice/noise_bench.py):
# Silero VAD inside Whisper drops crowd-only stretches; a low VAD threshold keeps soft speech; a higher
# no_speech threshold keeps murmur that the default 0.6 drops; the prompt biases toward PC-part words.
NOISY_HALL = dict(
    beam_size=3,
    vad_filter=True,
    vad_parameters=dict(threshold=0.3, min_speech_duration_ms=120, min_silence_duration_ms=500, speech_pad_ms=300),
    no_speech_threshold=0.8,
    condition_on_previous_text=False,
    initial_prompt='PC build help: motherboard, CPU_FAN header, SYS_FAN, RAM, DIMM slot, M.2, SATA, 24-pin, 8-pin EPS, front panel.',
)


class TtsEngine:
    def __init__(self, device):
        from kokoro import KModel, KPipeline

        self.lock = Lock()
        # KOKORO_DIR: a local copy of hexgrad/Kokoro-82M (config.json, kokoro-v1_0.pth, voices/), so the box
        # needs no Hub download. Checked against kokoro 0.9.4 on the GPU box.
        self.local_dir = os.getenv("KOKORO_DIR")
        if self.local_dir:
            model = KModel(repo_id="hexgrad/Kokoro-82M", config=os.path.join(self.local_dir, "config.json"),
                           model=os.path.join(self.local_dir, "kokoro-v1_0.pth")).to(device).eval()
            self.pipeline = KPipeline(lang_code="a", repo_id="hexgrad/Kokoro-82M", model=model, device=device)
        else:
            self.pipeline = KPipeline(lang_code="a", device=device)

    def segments(self, text, voice):
        if self.local_dir and not voice.endswith(".pt"):
            voice = os.path.join(self.local_dir, "voices", voice + ".pt")
        # Synthesize everything under the lock, then release it before yielding. A client that drops a
        # streamed response (barge-in) used to leave this generator suspended while holding the lock, and every
        # later request hung (seen live). Kokoro runs at about 1/100 real time, so one sentence costs ~40 ms.
        with self.lock:
            chunks = [audio for _, _, audio in self.pipeline(text, voice=voice)]
        yield from chunks


class Detector:
    def __init__(self, device):
        from ultralytics import YOLO

        self.lock = Lock()
        self.device = device
        self.model = YOLO(os.getenv("YOLO_WEIGHTS", "yolo11n.pt"))

    def detect(self, image, imgsz, conf):
        with self.lock:
            results = self.model.predict(
                source=image, imgsz=imgsz, conf=conf, device=self.device,
                half=self.device == "cuda", verbose=False, save=False,
            )
            return [
                {"label": result.names[int(cls)], "xyxy": box, "conf": score}
                for result in results
                for box, cls, score in zip(
                    result.boxes.xyxy.cpu().tolist(),
                    result.boxes.cls.cpu().tolist(),
                    result.boxes.conf.cpu().tolist(),
                )
            ]
