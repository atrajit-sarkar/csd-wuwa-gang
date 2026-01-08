from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .elevenlabs_client import ElevenLabsVoiceSettings


@dataclass(frozen=True)
class ElevenLabsVoiceProfile:
    voice_id: str
    model_id: str = "eleven_multilingual_v2"
    output_format: str = "mp3_44100_128"
    voice_settings: ElevenLabsVoiceSettings = ElevenLabsVoiceSettings()


def _env_key_for_character(character_name: str) -> str:
    # ELEVENLABS_VOICE_ID_LYNAE, ELEVENLABS_VOICE_ID_SHOREKEEPER, ...
    clean = "".join(c for c in (character_name or "") if c.isalnum() or c in {"_", "-"}).strip()
    clean = clean.replace("-", "_")
    return f"ELEVENLABS_VOICE_ID_{clean.upper()}"


def load_elevenlabs_voice_profile_for_character(*, character_name: str) -> Optional[ElevenLabsVoiceProfile]:
    """Resolve ElevenLabs voice config for a character.

    This intentionally does NOT hardcode voice IDs (those are account-specific).
    Instead, you configure per-character voice IDs via env vars.

    Env options:
    - ELEVENLABS_VOICE_ID_<CHARACTER>
    - ELEVENLABS_DEFAULT_VOICE_ID (fallback)
    - ELEVENLABS_MODEL_ID (optional override)
    - ELEVENLABS_OUTPUT_FORMAT (optional override)
    """

    per_char_key = _env_key_for_character(character_name)
    voice_id = (os.getenv(per_char_key, "") or "").strip()
    if not voice_id:
        voice_id = (os.getenv("ELEVENLABS_DEFAULT_VOICE_ID", "") or "").strip()
    if not voice_id:
        return None

    model_id = (os.getenv("ELEVENLABS_MODEL_ID", "") or "").strip() or "eleven_multilingual_v2"
    output_format = (os.getenv("ELEVENLABS_OUTPUT_FORMAT", "") or "").strip() or "mp3_44100_128"

    # Personality-aligned default voice settings.
    # (These are safe-ish defaults; tweak per your taste.)
    cname = (character_name or "").strip().lower()

    if cname in {"shorekeeper"}:
        settings = ElevenLabsVoiceSettings(stability=0.75, similarity_boost=0.7, style=0.15, use_speaker_boost=True)
    elif cname in {"cantarella"}:
        settings = ElevenLabsVoiceSettings(stability=0.55, similarity_boost=0.8, style=0.35, use_speaker_boost=True)
    elif cname in {"chisa"}:
        settings = ElevenLabsVoiceSettings(stability=0.8, similarity_boost=0.65, style=0.05, use_speaker_boost=True)
    elif cname in {"lynae", "linae"}:
        settings = ElevenLabsVoiceSettings(stability=0.45, similarity_boost=0.75, style=0.35, use_speaker_boost=True)
    else:
        settings = ElevenLabsVoiceSettings()

    return ElevenLabsVoiceProfile(
        voice_id=voice_id,
        model_id=model_id,
        output_format=output_format,
        voice_settings=settings,
    )
