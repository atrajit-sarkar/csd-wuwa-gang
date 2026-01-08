from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import httpx


class ElevenLabsAuthError(RuntimeError):
    pass


class ElevenLabsRateLimitError(RuntimeError):
    pass


class ElevenLabsServerError(RuntimeError):
    pass


@dataclass(frozen=True)
class ElevenLabsVoiceSettings:
    stability: float = 0.5
    similarity_boost: float = 0.75
    style: float = 0.0
    use_speaker_boost: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "stability": float(self.stability),
            "similarity_boost": float(self.similarity_boost),
            "style": float(self.style),
            "use_speaker_boost": bool(self.use_speaker_boost),
        }


@dataclass(frozen=True)
class ElevenLabsTTSRequest:
    voice_id: str
    text: str
    model_id: str = "eleven_multilingual_v2"
    output_format: str = "mp3_44100_128"
    voice_settings: Optional[ElevenLabsVoiceSettings] = None


async def tts_with_key_rotation(
    *,
    api_keys: Iterable[str],
    req: ElevenLabsTTSRequest,
    api_base: str = "https://api.elevenlabs.io",
    timeout_s: float = 60.0,
) -> bytes:
    """Call ElevenLabs TTS with key rotation.

    Tries keys in order; rotates on auth/rate-limit/server errors.
    Returns raw audio bytes.
    """

    voice_id = (req.voice_id or "").strip()
    if not voice_id:
        raise ValueError("voice_id must be a non-empty string")

    text = (req.text or "").strip()
    if not text:
        raise ValueError("text must be a non-empty string")

    url = f"{api_base.rstrip('/')}/v1/text-to-speech/{voice_id}"

    payload: dict[str, Any] = {
        "text": text,
        "model_id": req.model_id,
        "output_format": req.output_format,
    }
    if req.voice_settings is not None:
        payload["voice_settings"] = req.voice_settings.to_dict()

    last_error: Exception | None = None

    async with httpx.AsyncClient(timeout=timeout_s) as client:
        for api_key in api_keys:
            api_key = (api_key or "").strip()
            if not api_key:
                continue

            try:
                resp = await client.post(
                    url,
                    headers={
                        "xi-api-key": api_key,
                        "Accept": "audio/mpeg",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                )

                if resp.status_code in (401, 403):
                    raise ElevenLabsAuthError(f"Auth failed ({resp.status_code})")
                if resp.status_code == 429:
                    raise ElevenLabsRateLimitError("Rate limited (429)")
                if resp.status_code >= 500:
                    raise ElevenLabsServerError(f"Server error ({resp.status_code})")

                resp.raise_for_status()
                return resp.content

            except (ElevenLabsAuthError, ElevenLabsRateLimitError, ElevenLabsServerError, httpx.HTTPError, json.JSONDecodeError) as exc:
                last_error = exc
                continue

    raise RuntimeError(f"All ElevenLabs API keys failed; last error: {last_error!r}")
