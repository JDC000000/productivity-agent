"""Voice transcription via faster-whisper.

Phase 3b: Telegram voice messages -> local Whisper transcription -> text intent
parser. Runs entirely on this Mac, no API key needed.

Model is lazy-loaded on first call. The "small" variant (~150MB) is the sweet
spot for this use case: fast on Apple Silicon CPU, good enough quality for
~30-second voice memos. First call downloads the model weights into the
HuggingFace cache (~/.cache/huggingface/hub/) and may take ~30s; subsequent
calls reuse the in-process model and transcribe a 30s clip in 2-5s.
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

_MODEL_NAME = "small"
_COMPUTE_TYPE = "int8"  # CPU-friendly quantization; works on Apple Silicon
_model = None  # singleton, populated on first transcribe() call


def _get_model():
    """Lazy-load the faster-whisper model. Importing faster_whisper is also
    deferred until first use so the rest of the bot can boot without it."""
    global _model
    if _model is None:
        log.info(
            "Loading faster-whisper model: %s (first call may download ~150MB)",
            _MODEL_NAME,
        )
        from faster_whisper import WhisperModel

        _model = WhisperModel(_MODEL_NAME, device="cpu", compute_type=_COMPUTE_TYPE)
        log.info("faster-whisper model loaded.")
    return _model


def transcribe(audio_path: Path | str) -> str:
    """Transcribe an audio file (Telegram OGG/Opus is fine) to plain text.

    Returns the joined transcript with whitespace trimmed. Empty string if no
    speech was detected.
    """
    model = _get_model()
    segments, info = model.transcribe(str(audio_path), beam_size=5)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    log.info(
        "transcribed audio (lang=%s, duration=%.1fs): %r",
        info.language,
        info.duration,
        text,
    )
    return text
