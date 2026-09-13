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


# --- choosing a voice from the UI ---------------------------------------
#
# The browser may ask for a voice, so the request is re-checked server-side
# against the voices the account actually has.

KNOWN = [{"id": "voice_abc", "name": "Jessica", "description": "Playful, Bright, Warm"}]


def test_voices_lists_nothing_when_no_vendor_is_configured(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "browser")
    r = TestClient(app).get("/voices")
    assert r.status_code == 200
    assert r.json() == {"provider": "browser", "default": "", "voices": []}


def test_voices_lists_the_account_voices_for_the_ui(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice_abc")
    monkeypatch.setattr(tts_module, "_VOICE_CACHE", {})
    monkeypatch.setattr(tts_module, "_eleven_voices", lambda: KNOWN)
    body = TestClient(app).get("/voices").json()
    assert body["provider"] == "elevenlabs"
    assert body["default"] == "voice_abc"
    assert body["voices"] == KNOWN


def test_a_requested_voice_is_used_when_the_account_has_it(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
    monkeypatch.setattr(tts_module, "voices", lambda: KNOWN)
    seen = {}

    def capture(text, voice_id=None):
        seen["voice_id"] = voice_id
        return b"x", "audio/mpeg"

    monkeypatch.setattr(tts_module, "_elevenlabs", capture)
    result = tts_module.synthesize("Your order shipped.", voice_id="voice_abc")
    assert seen["voice_id"] == "voice_abc"
    assert result.voice == "voice_abc"


def test_an_unknown_voice_falls_back_to_the_default_and_never_reaches_the_vendor(monkeypatch):
    """A stale or forged id must not select a voice, and must not be pasted
    into the vendor URL. It costs the customer nothing: they get the default."""
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
    monkeypatch.setenv("ELEVENLABS_VOICE_ID", "voice_abc")
    monkeypatch.setattr(tts_module, "voices", lambda: KNOWN)
    seen = {}

    def capture(text, voice_id=None):
        seen["voice_id"] = voice_id
        return b"x", "audio/mpeg"

    monkeypatch.setattr(tts_module, "_elevenlabs", capture)
    result = tts_module.synthesize("Your order shipped.", voice_id="../../etc/passwd")
    assert seen["voice_id"] is None          # the default is applied downstream
    assert result.voice == "voice_abc"


def test_voice_choice_survives_the_round_trip_through_the_endpoint(monkeypatch):
    monkeypatch.setenv("TTS_PROVIDER", "elevenlabs")
    monkeypatch.setattr(tts_module, "voices", lambda: KNOWN)
    monkeypatch.setattr(tts_module, "_elevenlabs",
                        lambda text, voice_id=None: (b"ID3fake-mp3", "audio/mpeg"))
    r = TestClient(app).post("/tts", json={"text": "Hello.", "voice_id": "voice_abc"})
    assert r.status_code == 200
    assert r.headers["X-TTS-Voice"] == "voice_abc"
