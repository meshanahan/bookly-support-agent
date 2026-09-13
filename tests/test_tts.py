"""The /tts endpoint degrades to 204 without a vendor and never needs the network."""

from fastapi.testclient import TestClient

from app import tts as tts_module
from app.main import app


def test_tts_is_204_when_no_vendor_configured(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "browser")
    r = TestClient(app).post("/tts", json={"text": "Your order shipped."})
    assert r.status_code == 204


def test_tts_returns_audio_and_latency_header_when_vendor_answers(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
    monkeypatch.setattr(tts_module, "_elevenlabs", lambda text: (b"ID3fake-mp3", "audio/mpeg"))
    r = TestClient(app).post("/tts", json={"text": "Your order shipped."})
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("audio/mpeg")
    assert r.headers["X-TTS-Provider"] == "elevenlabs"
    assert int(r.headers["X-TTS-Latency-Ms"]) >= 0
    assert r.content == b"ID3fake-mp3"


def test_tts_degrades_to_204_when_vendor_fails(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")

    def boom(text):
        raise RuntimeError("vendor down")

    monkeypatch.setattr(tts_module, "_elevenlabs", boom)
    r = TestClient(app).post("/tts", json={"text": "Your order shipped."})
    assert r.status_code == 204


def test_synthesize_truncates_and_normalises_whitespace(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
    seen = {}

    def capture(text):
        seen["text"] = text
        return b"x", "audio/mpeg"

    monkeypatch.setattr(tts_module, "_elevenlabs", capture)
    tts_module.synthesize("a  b\n\nc " + "x" * 2000)
    assert seen["text"].startswith("a b c")
    assert len(seen["text"]) == tts_module.MAX_CHARS
