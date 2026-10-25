# GPU box speech and detection

Copy `boxsvc/` to the Windows GPU box inside a parent directory, then run
`powershell -File boxsvc/run_box.ps1`. This activates the environment configured by `BOX_VENV`
and binds the service to `PC_TAILSCALE_IP` on port 8090. Set the model paths
`STT_MODEL`, `YOLO_WEIGHTS` and `KOKORO_DIR` in the environment first.
The environment needs FastAPI, uvicorn, Pillow, and the installed model packages
(torch, numpy, faster-whisper, kokoro, ultralytics).

Deployment must listen on the tailnet only. The startup script refuses to run without an explicit
`PC_TAILSCALE_IP`; enforce tailnet-only access with the host firewall as well. There is no application authentication.

Models load once in a background thread. `GET /health` reports `ok`, readiness
flags `stt`, `tts`, `detect`, and `device` (`cuda` or `cpu`). Pending or failed
engines return 503 with a short reason; a missing model library does not stop
the app. CPU fallback uses int8 Whisper and full-precision detection.

- `POST /stt`: JSON `{"pcm16_b64":"...","language":"en"}`. Supply base64
  little-endian PCM16 at 16 kHz, mono, without a WAV header. Returns `text` and
  elapsed `ms`. `STT_MODEL` defaults to `large-v3-turbo`; a local model directory
  such as `<PC_WORK_DIR>\models\faster-whisper-large-v3-turbo` also works.
  CUDA uses fp16, beam size 1, and no VAD.
- `POST /tts`: JSON `{"text":"Hello","voice":"af_heart"}` (voice optional).
  Streams raw little-endian PCM16, 16 kHz mono as `application/octet-stream`,
  resampled from Kokoro's 24 kHz segments. First-chunk milliseconds are logged,
  not sent as a response header. HTTP transport may combine segment chunks.
- `POST /detect`: JSON `{"jpeg_b64":"...","imgsz":320,"conf":0.35}` (last
  two fields optional). Returns `objects` with `label`, normalized `[x,y,w,h]`
  `bbox`, and `conf`, plus elapsed `ms`. `YOLO_WEIGHTS` defaults to `yolo11n.pt`;
  CUDA uses fp16. These are generic YOLO classes, not motherboard headers.

Request audio, images, and text are never saved or logged by this service.
Model libraries may download/cache weights on first startup. Preload weights
for an offline demo. Each adapter serializes inference for its model.
Stream errors after response headers terminate the stream; clients should
handle interrupted playback. No GPU latency or library compatibility claim is
made by the fake-engine tests. WAN detection remains limited by network latency.
