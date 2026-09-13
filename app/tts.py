"""Text-to-speech behind one adapter, mirroring llm.py.

Rule (Kramer): the production audio leg is a vendor voice, not the browser's.
The cascaded pipeline (STT -> LLM -> TTS) is unchanged; only the last stage is
swapped. Latency is measured here and travels back with the audio.
Rule (one file to swap vendors): nothing else imports a TTS SDK or URL. The
provider is read from the environment at call time so tests can flip it.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass

import httpx

# ElevenLabs premade voice "Jessica": expressive, upbeat, conversational.
# Override with ELEVENLABS_VOICE_ID after listing voices (see README).
DEFAULT_ELEVEN_VOICE = "cgSgspJ2msm6clMCkdW9"
DEFAULT_ELEVEN_MODEL = "eleven_flash_v2_5"      # real-time model, ~75 ms
DEFAULT_OPENAI_MODEL = "gpt-4o-mini-tts"
DEFAULT_OPENAI_VOICE = "nova"
OPENAI_STYLE = (
    "Warm, upbeat customer-support voice with a smile in it. Friendly and quick, "
    "clear on numbers, names and email addresses. Never sing-song, never breathy."
)
MAX_CHARS = 600          # a support reply; longer text is truncated, not refused
TIMEOUT_S = 15.0


@dataclass
class TTSResult:
    audio: bytes
    mime: str
    latency_ms: int
    provider: str


def provider() -> str:
    """browser | elevenlabs | openai. 'browser' means the client speaks for itself."""
    return os.getenv("TTS_PROVIDER", "browser").strip().lower()


def enabled() -> bool:
    return provider() in ("elevenlabs", "openai")


def synthesize(text: str) -> TTSResult | None:
    """One TTS call. Returns None when no vendor is configured or text is empty."""
    if not enabled() or not text or not text.strip():
        return None
    clean = " ".join(text.split())[:MAX_CHARS]
    t0 = time.perf_counter()
    if provider() == "elevenlabs":
        audio, mime = _elevenlabs(clean)
    else:
        audio, mime = _openai(clean)
    return TTSResult(audio, mime, int((time.perf_counter() - t0) * 1000), provider())


def _elevenlabs(text: str) -> tuple[bytes, str]:
    voice_id = os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_ELEVEN_VOICE)
    model_id = os.getenv("ELEVENLABS_MODEL", DEFAULT_ELEVEN_MODEL)
    r = httpx.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
        params={"output_format": "mp3_44100_128"},
        headers={
            "xi-api-key": os.environ["ELEVENLABS_API_KEY"],
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
        },
        json={
            "text": text,
            "model_id": model_id,
            # Lower stability = more expressive; style adds energy. Tuned for
            # "bubbly but professional". Raise stability toward 0.6 if it wobbles.
            "voice_settings": {
                "stability": 0.45,
                "similarity_boost": 0.8,
                "style": 0.35,
                "use_speaker_boost": True,
            },
        },
        timeout=TIMEOUT_S,
    )
    r.raise_for_status()
    return r.content, "audio/mpeg"


def _openai(text: str) -> tuple[bytes, str]:
    from openai import OpenAI  # lazy: never imported in tests

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.audio.speech.create(
        model=os.getenv("OPENAI_TTS_MODEL", DEFAULT_OPENAI_MODEL),
        voice=os.getenv("OPENAI_TTS_VOICE", DEFAULT_OPENAI_VOICE),
        input=text,
        instructions=OPENAI_STYLE,
        response_format="mp3",
    )
    return resp.content, "audio/mpeg"
