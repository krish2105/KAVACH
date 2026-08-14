"""Record real wake-word audio for training (the v4 corpus).

Every model so far — v1, v2, v3 — was trained on synthesised speech only, and
all three are deaf to this microphone. Measured: the same utterance scores
**0.858 as a file and 0.019 through the mic**, and with the calibration ruler
corrected the user's own takes score 0.026–0.152 against a 0.30 floor while
ordinary speech reaches 0.048. Channel augmentation (RIRs, MUSAN noise) was a
real bug and a real improvement and did not close it. What remains is that the
model has never heard a human being.

This records the audio that fixes that, and its real job is **refusing bad
takes**. A training set is the one place a quietly-wrong sample is never caught
again: it does not fail, it does not raise, it just makes the model slightly
worse in a way no downstream measurement can attribute to it.

Clips are emitted at exactly :data:`CLIP_SECONDS`, matching the trainer's
``AugmentationConfig.clip_duration``, with the speech centred. Both matter.
A different length is silently padded or truncated rather than rejected, and a
word already near the edge is slid out of frame entirely by augmentation — in
either case the real audio contributes noise instead of signal, which is
exactly the failure this whole exercise exists to escape.

The audio written here is deliberately kept on disk, unlike wake-word audio at
runtime (§7). It is training data the user chose to record, it lives under
``wakeword/data/real/``, and it is gitignored.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import numpy as np

log = logging.getLogger("kavach.voice.wakerecord")

SAMPLE_RATE = 16_000

#: How long the microphone runs for one take. Generous on purpose: the tape
#: has to hold a slow start, and `check_take` refuses anything that reaches the
#: end rather than quietly keeping half a word.
RECORD_SECONDS = 4.0

#: The trainer's clip length (`AugmentationConfig.clip_duration`). Not a
#: preference — a clip of any other length is reshaped without complaint.
CLIP_SECONDS = 2.0

#: Where the corpus lives. Gitignored; see the module docstring.
REAL_DIR = Path(__file__).resolve().parents[2] / "wakeword" / "data" / "real"

_FRAME = int(0.02 * SAMPLE_RATE)          # 20 ms
#: Speech is this many times the room's own noise floor. Relative rather than
#: absolute so a quiet room and a noisy one are both handled.
_SPEECH_OVER_FLOOR = 4.0
#: ...but never below this, or room tone in a silent room reads as speech.
_MIN_SPEECH_RMS = 0.012
#: Below this the take is a whisper, whatever the room is doing.
_MIN_TAKE_RMS = 0.006
#: At or above this the converter clipped and the sample is distorted.
_CLIPPING_PEAK = 0.99
#: Speech ending within this of the tape's end was probably still going.
_EDGE_S = 0.15
#: Pauses shorter than this are inside one utterance, not between two. A stop
#: consonant is ~50-150ms; a gap between separate sounds is longer.
_GAP_BRIDGE_S = 0.2

_TAKE_RE = re.compile(r"^take_(\d{3,})\.wav$")


@dataclass
class TakeCheck:
    """Whether a take may join the corpus, and the clip if it may."""

    ok: bool
    reason: str = ""
    clip: np.ndarray | None = None


def _frame_rms(audio: np.ndarray) -> np.ndarray:
    usable = len(audio) - (len(audio) % _FRAME)
    if usable <= 0:
        return np.zeros(0, dtype=np.float32)
    frames = audio[:usable].reshape(-1, _FRAME)
    return np.sqrt(np.mean(frames.astype(np.float64) ** 2, axis=1))


def speech_bounds(audio: np.ndarray) -> tuple[int, int] | None:
    """The longest single utterance in the take, or None if it is silent.

    The **longest run**, not the first-to-last voiced frame. That distinction
    is the whole of this function. Measured through the real microphone, on 4
    seconds of an ordinary room:

        frame rms p20 0.00521 → threshold 0.02085, 45/201 frames above
        first-to-last:  (0.36s, 3.5s)  →  "too long (3.1s)"  →  refused

    Nothing was wrong with that take. A chair creak near the start and the word
    near the end were read as one continuous 3.1-second utterance, and every
    take of the first recording session was refused on that — zero of a hundred
    landed. A room is not silent either side of the thing you meant to say.

    Energy is measured against the take's own noise floor rather than a fixed
    level, so the same thresholds work in a quiet room at night and a noisy one
    at midday.
    """
    rms = _frame_rms(audio)
    if len(rms) == 0:
        return None

    # A low percentile, not the median. With the median, a take where speech
    # fills most of the tape puts the "noise floor" *inside the speech*, the
    # threshold goes above everything, and a long phrase reads as silence —
    # which is exactly wrong for the negatives, where long phrases are the
    # point.
    floor = float(np.percentile(rms, 20))
    threshold = max(_MIN_SPEECH_RMS, floor * _SPEECH_OVER_FLOOR)
    voiced = rms >= threshold
    if not voiced.any():
        return None

    best = _longest_run(voiced, bridge=int(_GAP_BRIDGE_S / 0.02))
    if best is None:
        return None
    start_frame, end_frame = best
    return start_frame * _FRAME, (end_frame + 1) * _FRAME


def _longest_run(voiced: np.ndarray, bridge: int) -> tuple[int, int] | None:
    """The longest run of voiced frames, treating gaps of `bridge` or fewer
    frames as part of the same utterance.

    The bridging matters as much as the run: "kav-ACH" has a stop in the
    middle, and splitting on it would keep half the word.
    """
    runs: list[tuple[int, int]] = []
    start: int | None = None
    gap = 0

    for i, is_voiced in enumerate(voiced):
        if is_voiced:
            if start is None:
                start = i
            gap = 0
            end = i
        elif start is not None:
            gap += 1
            if gap > bridge:
                runs.append((start, end))
                start = None
                gap = 0
    if start is not None:
        runs.append((start, end))

    if not runs:
        return None
    return max(runs, key=lambda r: r[1] - r[0])


def check_take(audio: np.ndarray) -> TakeCheck:
    """Accept a take and return its centred clip, or refuse it and say why.

    Each refusal is separate because each teaches the model something
    different and wrong: a whisper that the wake word is quiet, a shout that it
    is distorted, silence that the label is a lie, half a word that half a word
    is the wake word.
    """
    if len(audio) == 0:
        return TakeCheck(False, "nothing was recorded")

    peak = float(np.max(np.abs(audio)))
    if peak >= _CLIPPING_PEAK:
        return TakeCheck(False, "too loud — it clipped, move back a little")

    if float(np.sqrt(np.mean(audio.astype(np.float64) ** 2))) < _MIN_TAKE_RMS:
        return TakeCheck(False, "too quiet — nothing much above the room")

    bounds = speech_bounds(audio)
    if bounds is None:
        return TakeCheck(False, "nothing was said")

    start, end = bounds
    spoken_s = (end - start) / SAMPLE_RATE
    if spoken_s > CLIP_SECONDS:
        return TakeCheck(
            False, f"too long ({spoken_s:.1f}s) — just the wake word, nothing else"
        )

    if end >= len(audio) - int(_EDGE_S * SAMPLE_RATE):
        # The same fault the calibration ruler was fixed for, and worse here:
        # in a measurement half a word scores low, in a training set it is
        # learned as the target.
        return TakeCheck(False, "started too late — it was cut off at the end")

    return TakeCheck(True, "", _centre(audio, start, end))


def _centre(audio: np.ndarray, start: int, end: int) -> np.ndarray:
    """A CLIP_SECONDS clip with the speech in the middle.

    Padded with silence rather than with whatever the tape happened to hold
    next: the point is that augmentation can shift the word without pushing it
    out of frame, and that only holds if the margins are actually margins.
    """
    want = int(CLIP_SECONDS * SAMPLE_RATE)
    middle = (start + end) // 2
    begin = middle - want // 2

    clip = np.zeros(want, dtype=np.float32)
    src_start = max(0, begin)
    src_end = min(len(audio), begin + want)
    dst_start = src_start - begin
    clip[dst_start : dst_start + (src_end - src_start)] = audio[src_start:src_end]
    return clip


def next_index(directory: Path) -> int:
    """The number the next take should carry.

    A hundred takes is more than one sitting, so a second run continues the
    corpus rather than overwriting it. Reads the directory instead of keeping a
    counter — a counter in a file is a second source of truth that goes stale
    the first time someone deletes a bad take by hand.
    """
    directory = Path(directory)
    if not directory.exists():
        return 0

    highest = -1
    for path in directory.glob("*.wav"):
        match = _TAKE_RE.match(path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def save_clip(clip: np.ndarray, directory: Path, index: int) -> Path:
    """Write one 16-bit mono clip. Returns the path."""
    import wave

    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"take_{index:03d}.wav"

    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(SAMPLE_RATE)
        handle.writeframes((np.clip(clip, -1.0, 1.0) * 32767).astype("<i2").tobytes())
    return path
