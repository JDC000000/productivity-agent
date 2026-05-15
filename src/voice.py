"""Voice transcription via faster-whisper.

Phase 3b: Telegram voice messages -> local Whisper transcription -> text intent
parser. Runs entirely on this Mac, no API key needed.

Phase 2 (MUST): transcribe() now also returns a confidence score derived from
faster-whisper's per-segment avg_logprob (duration-weighted). The bot uses this
to tier behavior: HIGH = silent, MEDIUM = 5s cancel window, LOW = refuse.

Model is lazy-loaded on first call. The "small" variant (~150MB) is the sweet
spot for this use case: fast on Apple Silicon CPU, good enough quality for
~30-second voice memos. First call downloads the model weights into the
HuggingFace cache (~/.cache/huggingface/hub/) and may take ~30s; subsequent
calls reuse the in-process model and transcribe a 30s clip in 2-5s.
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

_MODEL_NAME = "small"
_COMPUTE_TYPE = "int8"  # CPU-friendly quantization; works on Apple Silicon
_model = None  # singleton, populated on first transcribe() call

# Phase 2 (MUST) confidence tiering. Tune these from the audit log after a few
# weeks of real-world data.
CONFIDENCE_THRESHOLD = 0.6   # nominal "borderline" point in [0, 1]
CONFIDENCE_MARGIN = 0.1      # half-width of the MEDIUM band around the threshold
# Resulting tiers:
#   HIGH:   score >= THRESHOLD + MARGIN   (>= 0.7)   — silent, no confirmation
#   MEDIUM: THRESHOLD - MARGIN <= score < THRESHOLD + MARGIN  (0.5..0.7) — 5s cancel window
#   LOW:    score < THRESHOLD - MARGIN    (< 0.5)    — refuse, ask for resend


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


def _segment_confidence(segments: list[Any]) -> float:
    """Duration-weighted exp(avg_logprob) in [0, 1].

    faster-whisper's Segment.avg_logprob is the mean log-probability of the
    decoded tokens in that segment. exp() turns it into a perplexity-inverse
    score where 1.0 = perfect, 0.0 = nothing. We weight by duration so a long
    clean segment isn't pulled down by a 0.2s noise burst.

    Empty input returns 0.0 (treated as LOW by the tiering logic).
    """
    total_weight = 0.0
    weighted_logprob_sum = 0.0
    for seg in segments:
        avg_lp = getattr(seg, "avg_logprob", None)
        if avg_lp is None:
            continue
        duration = max(0.01, getattr(seg, "end", 0.0) - getattr(seg, "start", 0.0))
        weighted_logprob_sum += avg_lp * duration
        total_weight += duration
    if total_weight == 0:
        return 0.0
    return math.exp(weighted_logprob_sum / total_weight)


def transcribe(audio_path: Path | str) -> tuple[str, float]:
    """Transcribe an audio file (Telegram OGG/Opus is fine).

    Returns (text, confidence) where:
      - text: joined transcript with whitespace trimmed; "" if no speech
      - confidence: duration-weighted exp(avg_logprob) in [0, 1]; 0.0 if empty

    Phase 2 (MUST) made this return a tuple. The single existing caller
    (voice_message in bot.py) was updated in lockstep.
    """
    model = _get_model()
    # faster-whisper returns a generator for segments; materialize so we can
    # both join the text and compute confidence in one pass without re-running
    # transcription.
    segment_iter, info = model.transcribe(str(audio_path), beam_size=5)
    segments = list(segment_iter)
    text = " ".join(seg.text.strip() for seg in segments).strip()
    confidence = _segment_confidence(segments)
    log.info(
        "transcribed audio (lang=%s, duration=%.1fs, confidence=%.2f): %r",
        info.language,
        info.duration,
        confidence,
        text,
    )
    return text, confidence
