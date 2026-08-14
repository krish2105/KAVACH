"""`kavach-wakerecord` — record real wake-word audio to train v4 on.

Spoken-guided, like enrolment and calibration, so it needs no keyboard while
you are talking. Every take is checked before it is kept and a refused take is
simply retried, because the cost of a bad sample here is not a failure — it is
a slightly worse model that nothing downstream can blame.

    uv run kavach-wakerecord                 # 100 wake takes, 50 other
    uv run kavach-wakerecord --positives 20  # a shorter sitting
    uv run kavach-wakerecord --status        # what has been recorded so far

Stop whenever you like: a later run continues from where the corpus ends.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

import numpy as np

from .loop import DEFAULT_MODELS_DIR
from .mic import MicStream
from .wakerecord import (
    CLIP_SECONDS,
    RECORD_SECONDS,
    REAL_DIR,
    check_take,
    next_index,
    save_clip,
)

RULE = "─" * 62

#: What to say for the negatives. Ordinary speech, plus the near-misses that
#: matter most — a model that fires on "car wash" is worse than one that
#: misses, because it starts recording the room unprompted.
NEGATIVE_PROMPTS = [
    "what time is it",
    "open my calendar for tomorrow",
    "play something quieter",
    "delete the draft in notes",
    "the quick brown fox jumps over the lazy dog",
    "cover it",
    "car wash",
    "come back",
    "how much time do I have",
    "call Vatsal",
]


def _record(mic: MicStream, seconds: float) -> np.ndarray:
    blocks: list[np.ndarray] = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        block = mic.read(timeout=0.5)
        if block is not None:
            blocks.append(block)
    return np.concatenate(blocks) if blocks else np.zeros(0, dtype=np.float32)


def _session(mic, directory, wanted: int, prompt_for, say, tone, label: str,
             max_attempts_each: int = 3) -> int:
    """Record `wanted` good takes into `directory`. Returns how many landed."""
    directory.mkdir(parents=True, exist_ok=True)
    kept = 0

    while kept < wanted:
        index = next_index(directory)
        spoken = prompt_for(kept)["spoken"]
        # An empty prompt means the tone alone. For the wake word that is the
        # point: hearing Kokoro say "KAVACH" before every take would pull the
        # speaker towards a synthetic pronunciation, and a corpus of someone
        # imitating a TTS voice is the problem this recording exists to escape.
        if spoken:
            say(spoken)

        for attempt in range(1, max_attempts_each + 1):
            tone()
            mic.forget()  # never record KAVACH's own prompt
            audio = _record(mic, RECORD_SECONDS)
            result = check_take(audio)

            if result.ok:
                path = save_clip(result.clip, directory, index)
                kept += 1
                print(f"  [{kept}/{wanted}] {label:8} {path.name}   ✓")
                break

            print(f"  [{kept + 1}/{wanted}] {label:8} refused — {result.reason}")
            if attempt < max_attempts_each:
                say("Again please.")
        else:
            # Three refusals in a row is a room problem, not a take problem.
            print(f"\n  ✗ three refused in a row: {result.reason}")
            say("Something is wrong with the audio. Stopping here.")
            return kept

    return kept


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Record real wake-word audio for training."
    )
    parser.add_argument("--positives", type=int, default=100,
                        help="how many takes of the wake word (default 100)")
    parser.add_argument("--negatives", type=int, default=50,
                        help="how many ordinary phrases (default 50)")
    parser.add_argument("--status", action="store_true",
                        help="show what has been recorded so far")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.WARNING, format="%(message)s")

    positive_dir = REAL_DIR / "positive"
    negative_dir = REAL_DIR / "negative"

    if args.status:
        print(f"  wake takes  {next_index(positive_dir):4}")
        print(f"  other takes {next_index(negative_dir):4}")
        print(f"  {REAL_DIR}")
        return 0

    from . import tts as tts_mod

    speaker = tts_mod.TextToSpeech(DEFAULT_MODELS_DIR)
    speaker.load()

    def say(text: str) -> None:
        tts_mod.play(speaker.synthesize(text), blocking=True)

    def tone(freq: int = 880, ms: int = 130) -> None:
        rate = 24_000
        t = np.linspace(0, ms / 1000, int(rate * ms / 1000), endpoint=False)
        beep = (0.22 * np.sin(2 * np.pi * freq * t)).astype(np.float32)
        tts_mod.play(
            tts_mod.Speech(audio=beep, sample_rate=rate, voice="tone"), blocking=True
        )

    already_p, already_n = next_index(positive_dir), next_index(negative_dir)

    print(RULE)
    print("  KAVACH wake-word recording — real audio for v4")
    print(RULE)
    print(f"  {args.positives} takes of the wake word, then {args.negatives} ordinary phrases.")
    print(f"  Each take: a tone, then speak straight away. {RECORD_SECONDS:.0f}s of tape,")
    print(f"  trimmed to {CLIP_SECONDS:.0f}s around what you said.")
    if already_p or already_n:
        print(f"  Continuing a corpus of {already_p} wake / {already_n} other.")
    print()
    print("  Speak exactly as you would in real use — same distance, same volume.")
    print("  Vary it a little: turn your head, sit back, say it fast and slow.")
    print(f"  Saved to {REAL_DIR}")
    print(RULE)

    say("Wake word recording. After each tone, say KAVACH, straight away. "
        "Vary how you say it a little.")

    mic = MicStream().start()
    kept_p = kept_n = 0
    try:
        #: Tone only, except for an occasional nudge. Variety in the corpus is
        #: what stops the model learning one fixed delivery, and it will not
        #: happen on its own over a hundred identical prompts.
        nudges = [
            "Keep going.",
            "Say it a bit faster this time.",
            "Now a little quieter, as if it were late.",
            "Turn your head away slightly.",
            "Sit back a bit further.",
            "Normally again.",
        ]
        kept_p = _session(
            mic, positive_dir, args.positives,
            prompt_for=lambda n: {
                "spoken": nudges[(n // 10) % len(nudges)] if n and n % 10 == 0 else ""
            },
            say=say, tone=tone, label="kavach",
        )

        if kept_p >= args.positives and args.negatives > 0:
            say("Now some ordinary phrases, so I can tell them apart.")
            kept_n = _session(
                mic, negative_dir, args.negatives,
                prompt_for=lambda n: {
                    "spoken": f"Say: {NEGATIVE_PROMPTS[n % len(NEGATIVE_PROMPTS)]}"
                },
                say=say, tone=tone, label="other",
            )
    except KeyboardInterrupt:
        print("\n  stopped — everything recorded so far is kept")
    finally:
        mic.stop()

    print()
    print(RULE)
    print(f"  wake takes  {next_index(positive_dir):4}  (+{kept_p} this session)")
    print(f"  other takes {next_index(negative_dir):4}  (+{kept_n} this session)")
    print(RULE)

    if next_index(positive_dir) < 50:
        print("  Fewer than 50 wake takes. Against 10000 synthetic clips that is")
        print("  unlikely to move the model — run it again when you have time.")
        return 1

    say("Recording done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
