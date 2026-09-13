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
OPENAI_VOICES = ("alloy", "ash", "ballad", "coral", "echo", "fable", "nova",
                 "onyx", "sage", "shimmer")

# Keyed by provider so flipping TTS_PROVIDER cannot serve one vendor's list for
# another. Only successful lookups are cached; a failed one stays retryable.
_VOICE_CACHE: dict[str, list[dict[str, str]]] = {}


@dataclass
class TTSResult:
    audio: bytes
    mime: str
    latency_ms: int
    provider: str
    voice: str = ""


def provider() -> str:
    """browser | elevenlabs | openai. 'browser' means the client speaks for itself."""
    return os.getenv("TTS_PROVIDER", "browser").strip().lower()


def enabled() -> bool:
    return provider() in ("elevenlabs", "openai")


def default_voice() -> str:
    """The voice used when the caller does not ask for one."""
    if provider() == "openai":
        return os.getenv("OPENAI_TTS_VOICE", DEFAULT_OPENAI_VOICE)
    return os.getenv("ELEVENLABS_VOICE_ID", DEFAULT_ELEVEN_VOICE)


def voices() -> list[dict[str, str]]:
    """The voices this account can use: [{id, name, description}].

    Empty when no vendor is configured or the vendor cannot be reached — the UI
    then simply offers no choice, which is the same outcome as before.
    """
    key = provider()
    if key in _VOICE_CACHE:
        return _VOICE_CACHE[key]
    if key == "openai":
        found = [{"id": v, "name": v, "description": ""} for v in OPENAI_VOICES]
    elif key == "elevenlabs":
        try:
            found = _eleven_voices()
        except Exception:
            return []          # transient: do not cache, let the next call retry
    else:
        found = []
    _VOICE_CACHE[key] = found
    return found


def _eleven_voices() -> list[dict[str, str]]:
    r = httpx.get(
        "https://api.elevenlabs.io/v1/voices",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"]},
        timeout=TIMEOUT_S,
    )
    r.raise_for_status()
    out = []
    for v in r.json().get("voices", []):
        # Names arrive as "Jessica - Playful, Bright, Warm".
        name, _, description = str(v.get("name", "")).partition(" - ")
        out.append({"id": v["voice_id"], "name": name.strip(),
                    "description": description.strip()})
    return out


def synthesize(text: str, voice_id: str | None = None) -> TTSResult | None:
    """One TTS call. Returns None when no vendor is configured or text is empty.

    `voice_id` comes from the browser, so it is never passed through on trust:
    it is used only if it matches a voice this account actually has. Anything
    else falls back to the configured default rather than erroring, because a
    stale selection should cost the customer nothing. That check also keeps
    client input out of the vendor URL path.
    """
    if not enabled() or not text or not text.strip():
        return None
    clean = " ".join(text.split())[:MAX_CHARS]
    chosen = voice_id if voice_id and _is_known_voice(voice_id) else None
    t0 = time.perf_counter()
    if provider() == "elevenlabs":
        # Called with one argument when there is no override, so a test may
        # stand in for this with a single-parameter fake.
        audio, mime = _elevenlabs(clean, chosen) if chosen else _elevenlabs(clean)
    else:
        audio, mime = _openai(clean, chosen) if chosen else _openai(clean)
    return TTSResult(audio, mime, int((time.perf_counter() - t0) * 1000), provider(),
                     chosen or default_voice())


def _is_known_voice(voice_id: str) -> bool:
    """Only consults the vendor when an override was actually supplied, and the
    list is already warm because the UI fetched it to build its menu."""
    return any(v["id"] == voice_id for v in voices())


def _elevenlabs(text: str, voice_id: str | None = None) -> tuple[bytes, str]:
    voice_id = voice_id or default_voice()
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


def _openai(text: str, voice_id: str | None = None) -> tuple[bytes, str]:
    from openai import OpenAI  # lazy: never imported in tests

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.audio.speech.create(
        model=os.getenv("OPENAI_TTS_MODEL", DEFAULT_OPENAI_MODEL),
        voice=voice_id or default_voice(),
        input=text,
        instructions=OPENAI_STYLE,
        response_format="mp3",
    )
    return resp.content, "audio/mpeg"
