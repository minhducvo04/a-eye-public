"""Keep tests hermetic: `make` exports .env, so clear runtime switches and keys before each test."""
import pytest

RUNTIME_ENV = [
    "GOOGLE_CLIENT_ID", "SESSION_SECRET",
    "GEMINI_API_KEY", "GEMINI_LIVE_MODEL",
    "A_EYE_TOKEN", "CF_ACCESS_TEAM_DOMAIN", "CF_ACCESS_AUD", "A_EYE_OPEN", "A_EYE_DEMO_OPEN",
    "VISION_BACKEND", "VISION_URL", "VISION_MODEL", "OLLAMA_URL", "FAKE_SCENARIO",
    "LLM_PROVIDER", "ANTHROPIC_API_KEY", "ANTHROPIC_MODEL", "LOCAL_LLM_MODEL", "LOCAL_ONLY",
    "VOICE", "CARTESIA_API_KEY", "CARTESIA_VOICE_ID", "FAKE_TRANSCRIPT",
    "MARKET_SOURCE", "APIFY_TOKEN", "BRIGHTDATA_API_KEY", "BRIGHTDATA_ZONE",
    "BOX_URL", "YOLO_REFRESH", "STT_MODEL", "YOLO_WEIGHTS", "KOKORO_DIR", "TWELVELABS_API_KEY", "A_EYE_MEMORY_KEY", "A_EYE_MEMORY_DIR", "A_EYE_MEMORY_DAYS",
]
# The GPU box is live on the tailnet now; point tests at a closed local port so nothing reaches it.
UNREACHABLE_BOX = "http://127.0.0.1:9"


@pytest.fixture(autouse=True)
def _hermetic_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUD_BUDGET_PATH", str(tmp_path / "cloud-budget.sqlite3"))
    for name in ("DAILY_CLOUD_CAP_USD", "TURNS_PER_MINUTE", "CALLS_PER_IP", "MAX_OPEN_SOCKETS"):
        monkeypatch.delenv(name, raising=False)
    from server import cloud_meter, privacy
    original_local = cloud_meter.is_local
    from urllib.parse import urlsplit
    monkeypatch.setattr(cloud_meter, 'is_local', lambda url: urlsplit(str(url)).hostname == 'testserver' or original_local(url))
    privacy.PRIVATE.set(False)
    privacy.SWITCHES.set(None)
    cloud_meter.CURRENT.set(None)
    for name in RUNTIME_ENV:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BOX_URL", UNREACHABLE_BOX)
    # Default to a direct in-process caller; security tests supply proxy headers or an explicit remote client.
    from fastapi.testclient import TestClient
    original = TestClient.__init__
    def local_client(self, *args, **kwargs):
        kwargs.setdefault('client', ('127.0.0.1', 50000))
        original(self, *args, **kwargs)
    monkeypatch.setattr(TestClient, '__init__', local_client)


# Some modules pick a backend at import time, so also clear the switches before any test module loads.
import os  # noqa: E402

for _name in RUNTIME_ENV:
    os.environ.pop(_name, None)
os.environ["BOX_URL"] = UNREACHABLE_BOX
