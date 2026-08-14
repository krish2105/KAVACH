"""Text-to-speech via Kokoro (ONNX).

Piper was archived in Oct 2025; Kokoro is the current best local voice (spec
§3). `kokoro-onnx` bundles espeak through `espeakng-loader`, so there is no
system espeak-ng to install.

Unlike Whisper, Kokoro does not fetch its own weights — the caller supplies
both files, so `ensure_models()` downloads them once into `models/`, which is
gitignored.
"""

from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("kavach.voice.tts")

# Pinned release assets — a moving "latest" would silently change the voice.
MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
    "kokoro-v1.0.onnx"
)
VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
    "voices-v1.0.bin"
)

DEFAULT_VOICE = "af_heart"
DEFAULT_SPEED = 1.1  # slightly brisk; a default-paced assistant feels sluggish
SAMPLE_RATE = 24_000  # Kokoro's native output rate


@dataclass
class Speech:
    audio: np.ndarray
    sample_rate: int
    voice: str


def _download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0:
        return
    log.info("downloading %s → %s", url.rsplit("/", 1)[-1], dest)
    tmp = dest.with_suffix(dest.suffix + ".part")
    urllib.request.urlretrieve(url, tmp)
    # Rename only on success, so an interrupted download can't masquerade as a
    # complete model file on the next run.
    tmp.rename(dest)


def ensure_models(models_dir: Path) -> tuple[Path, Path]:
    model = models_dir / "kokoro-v1.0.onnx"
    voices = models_dir / "voices-v1.0.bin"
    _download(MODEL_URL, model)
    _download(VOICES_URL, voices)
    return model, voices


class TextToSpeech:
    def __init__(
        self,
        models_dir: Path,
        voice: str = DEFAULT_VOICE,
        speed: float = DEFAULT_SPEED,
    ):
        self.models_dir = Path(models_dir)
        self.voice = voice
        self.speed = speed
        self._kokoro = None

    def load(self) -> None:
        if self._kokoro is not None:
            return
        from kokoro_onnx import Kokoro

        model, voices = ensure_models(self.models_dir)
        log.info("loading Kokoro (%s)", self.voice)
        self._kokoro = Kokoro(str(model), str(voices))
        log.info("Kokoro ready")

    def available_voices(self) -> list[str]:
        if self._kokoro is None:
            self.load()
        assert self._kokoro is not None
        return sorted(self._kokoro.get_voices())

    def synthesize(self, text: str, voice: str | None = None,
                   language: str | None = None) -> Speech:
        """Speak `text`, in `language` if Kokoro has a voice for it.

        An explicit `voice` always wins; otherwise the language decides. Both
        the voice and the espeak code have to change together — the phonemiser
        needs the right language or a Hindi sentence comes out as English
        phonemes read aloud.
        """
        if self._kokoro is None:
            self.load()
        assert self._kokoro is not None

        from .languages import voice_for

        mapped = voice_for(language)
        chosen = voice or (mapped.voice if language else self.voice)
        audio, sample_rate = self._kokoro.create(
            text, voice=chosen, speed=self.speed, lang=mapped.espeak
        )
        return Speech(
            audio=np.asarray(audio, dtype=np.float32),
            sample_rate=int(sample_rate),
            voice=chosen,
        )


def play(speech: Speech, blocking: bool = True) -> None:
    import sounddevice as sd

    sd.play(speech.audio, speech.sample_rate)
    if blocking:
        sd.wait()


def last_playback_status():
    """PortAudio's callback flags from the last finished playback, or None.

    An underflow here is the difference between "the audio we generated was
    wrong" and "the audio we generated never reached the speakers in time".
    Nothing else in the stack can tell those apart, and they have opposite
    fixes.
    """
    import sounddevice as sd

    try:
        return sd.get_status()
    except Exception:
        return None


def stop_playback() -> None:
    """Cut audio immediately.

    Wired to Esc and to the kill switch: §5 is explicit that an assistant you
    cannot interrupt stops feeling like a presence and starts feeling like a
    hung process.
    """
    import sounddevice as sd

    sd.stop()


def envelope(audio: np.ndarray, sample_rate: int, hop_ms: int = 40) -> list[float]:
    """Per-hop RMS, normalised to 0–1, for driving the orb while speaking."""
    hop = max(1, int(sample_rate * hop_ms / 1000))
    frames = [
        float(np.sqrt(np.mean(audio[i : i + hop] ** 2)))
        for i in range(0, len(audio), hop)
    ]
    if not frames:
        return []
    peak = max(frames) or 1.0
    return [min(1.0, f / peak) for f in frames]
