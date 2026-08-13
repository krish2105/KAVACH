"""Voiceprint enrolment (spec §7 extension).

    uv run kavach-enrol            # record 5 phrases, build the profile
    uv run kavach-enrol --status   # is a profile enrolled? what threshold?
    uv run kavach-enrol --forget   # delete it

Needs the user physically present, so it is a deliberate one-off step rather
than anything automatic. Until it is run, confirmations check *what* was said
but not *who* said it, and the daemon says so at startup rather than implying
otherwise.
"""

from __future__ import annotations

import argparse
import sys
import time

import numpy as np

from ..voice.mic import TARGET_RATE, MicStream
from .voiceprint import MIN_ENROLMENT_SECONDS, Voiceprint

#: Varied phonetically so the profile is not overfitted to one sentence.
PHRASES = [
    "KAVACH, open my calendar for tomorrow",
    "The quick brown fox jumps over the lazy dog",
    "Delete the draft in Notes — yes, confirm that",
    "What is the weather going to be like this weekend",
    "Six thick slabs of blue granite were quarried early",
]

CLIP_SECONDS = 3.5


def _record(mic: MicStream, seconds: float) -> np.ndarray:
    """Record a fixed window. Fixed rather than silence-detected because an
    enrolment clip should be a known length, not however long you paused."""
    wanted = int(TARGET_RATE * seconds)
    chunks: list[np.ndarray] = []
    collected = 0
    deadline = time.monotonic() + seconds + 3.0

    while collected < wanted and time.monotonic() < deadline:
        block = mic.read(timeout=1.0)
        if block is None:
            continue
        chunks.append(block)
        collected += len(block)

    return np.concatenate(chunks)[:wanted] if chunks else np.zeros(0, dtype=np.float32)


def _run_spoken(voiceprint: Voiceprint, phrases: list[str]) -> int:
    """Audio-guided enrolment — KAVACH speaks the prompts.

    The Enter-driven flow needs a terminal to prompt into, which rules out
    running it from anywhere that isn't a TTY. Here KAVACH reads each phrase
    aloud and cues you with a tone, so enrolment works with the screen ignored
    entirely — which is also just a better experience for a voice assistant.
    """
    from ..voice import tts as tts_mod
    from ..voice.loop import DEFAULT_MODELS_DIR

    print("─" * 64)
    print("  KAVACH voiceprint enrolment — spoken mode")
    print("─" * 64)
    print("  Listen. KAVACH reads each phrase, then a tone means speak.")
    print(f"  {len(phrases)} phrases, {CLIP_SECONDS:.1f}s each.")
    print("─" * 64, flush=True)

    # Same models directory the voice loop uses, so enrolment reuses the
    # already-downloaded Kokoro weights rather than fetching a second copy.
    speaker = tts_mod.TextToSpeech(DEFAULT_MODELS_DIR)
    speaker.load()

    def say(text: str) -> None:
        tts_mod.play(speaker.synthesize(text), blocking=True)

    # A short sine cue. Unmistakably not speech, so it can't be mistaken for
    # part of the phrase you are meant to repeat.
    def tone(freq: float = 880.0, seconds: float = 0.18) -> None:
        t = np.linspace(0, seconds, int(TARGET_RATE * seconds), endpoint=False)
        envelope = np.minimum(1.0, np.minimum(t, seconds - t) * 40)
        beep = (0.25 * np.sin(2 * np.pi * freq * t) * envelope).astype(np.float32)
        tts_mod.play(tts_mod.Speech(audio=beep, sample_rate=TARGET_RATE,
                                    voice="tone"), blocking=True)

    say("Voice enrolment. I'll read five phrases. After each tone, repeat it "
        "in your normal speaking voice.")

    mic = MicStream().start()
    clips: list[np.ndarray] = []
    try:
        for i, phrase in enumerate(phrases, 1):
            print(f"\n  [{i}/{len(phrases)}]  \"{phrase}\"", flush=True)
            say(f"Phrase {i}. {phrase}")
            tone()
            # Drop everything buffered while KAVACH was talking, so its own
            # voice cannot end up inside your voiceprint.
            time.sleep(0.15)
            mic.forget()
            print("         ● recording…", end="", flush=True)
            clip = _record(mic, CLIP_SECONDS)
            tone(freq=440.0, seconds=0.12)
            rms = float(np.sqrt(np.mean(clip**2))) if len(clip) else 0.0
            if rms < 0.01:
                print(f" too quiet (rms {rms:.4f}) — skipped")
                say("That was too quiet. Skipping it.")
                continue
            clips.append(clip)
            print(f" done ({len(clip)/TARGET_RATE:.1f}s, level {rms:.3f})", flush=True)
    finally:
        mic.stop()

    total = sum(len(c) for c in clips) / TARGET_RATE
    if total < MIN_ENROLMENT_SECONDS:
        print(f"\n✗ only got {total:.1f}s of usable audio "
              f"(need {MIN_ENROLMENT_SECONDS:.0f}s).")
        say("I didn't get enough clear audio to build a profile. "
            "Check the microphone and try again.")
        return 1

    print(f"\n  building profile from {total:.1f}s…", flush=True)
    voiceprint.enrol(clips, sample_rate=TARGET_RATE)
    _report(voiceprint)
    say("Enrolment complete. Destructive actions now need your voice.")
    return 0


def _report(voiceprint: Voiceprint) -> None:
    print("─" * 64)
    print(f"  ✓ enrolled. threshold {voiceprint.threshold:.3f} "
          f"({'calibrated from your clips' if voiceprint.calibrated else 'fallback'})")
    print(f"    profile: {voiceprint.path} (0600, gitignored)")
    print()
    print("  Destructive actions now require an affirmative answer IN YOUR VOICE.")
    print("  If it ever refuses you wrongly, every attempt is logged with its")
    print("  similarity score — check ~/.kavach/logs/actions.jsonl and retune.")
    print("─" * 64)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kavach-enrol",
        description="Bind KAVACH's confirmation gate to your voice (§7).",
    )
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--forget", action="store_true")
    parser.add_argument("--clips", type=int, default=len(PHRASES))
    parser.add_argument("--spoken", action="store_true",
                        help="KAVACH speaks the prompts; no keyboard needed. "
                             "Used automatically when stdin is not a terminal.")
    parser.add_argument("--replace", action="store_true",
                        help="overwrite an existing profile without asking")
    args = parser.parse_args(argv)

    voiceprint = Voiceprint()

    if args.status:
        if voiceprint.is_enrolled:
            print(f"✓ enrolled from {voiceprint.enrolled_seconds:.1f}s of speech")
            print(f"  threshold: {voiceprint.threshold:.3f} "
                  f"({'calibrated' if voiceprint.calibrated else 'fallback'})")
            print(f"  profile:   {voiceprint.path}")
        else:
            print("✗ not enrolled — confirmations check WHAT you said, not WHO")
            print("  run `uv run kavach-enrol` to fix that")
        return 0

    if args.forget:
        voiceprint.forget()
        print("✓ voiceprint deleted. Confirmations no longer check identity.")
        return 0

    # No TTY means nothing can answer an `input()` prompt, so fall back to the
    # spoken flow rather than crashing on EOF.
    spoken = args.spoken or not sys.stdin.isatty()

    if voiceprint.is_enrolled:
        print(f"A profile already exists ({voiceprint.enrolled_seconds:.1f}s, "
              f"threshold {voiceprint.threshold:.3f}).")
        if not args.replace:
            if spoken:
                print("Refusing to overwrite it unattended. "
                      "Re-run with --replace if that is what you want.")
                return 1
            if input("Replace it? [y/N] ").strip().lower() not in {"y", "yes"}:
                print("Left unchanged.")
                return 0

    phrases = PHRASES[: max(2, args.clips)]

    if spoken:
        return _run_spoken(voiceprint, phrases)
    print("─" * 64)
    print("  KAVACH voiceprint enrolment")
    print("─" * 64)
    print(f"  {len(phrases)} phrases, {CLIP_SECONDS:.0f}s each "
          f"(~{len(phrases) * CLIP_SECONDS:.0f}s total).")
    print("  Speak normally, at the distance and volume you'd actually use.")
    print("  Recording starts right after you press Enter.")
    print("─" * 64)

    mic = MicStream().start()
    clips: list[np.ndarray] = []
    try:
        for i, phrase in enumerate(phrases, 1):
            input(f"\n  [{i}/{len(phrases)}]  \"{phrase}\"\n         press Enter, then speak…")
            mic.forget()  # drop whatever was buffered while you were reading
            print("         ● recording…", end="", flush=True)
            clip = _record(mic, CLIP_SECONDS)
            rms = float(np.sqrt(np.mean(clip**2))) if len(clip) else 0.0
            if rms < 0.01:
                print(f" too quiet (rms {rms:.4f}) — retaking")
                continue
            clips.append(clip)
            print(f" done ({len(clip)/TARGET_RATE:.1f}s, level {rms:.3f})")
    finally:
        mic.stop()

    total = sum(len(c) for c in clips) / TARGET_RATE
    if total < MIN_ENROLMENT_SECONDS:
        print(f"\n✗ only got {total:.1f}s of usable audio "
              f"(need {MIN_ENROLMENT_SECONDS:.0f}s). Check the mic and retry.")
        return 1

    print(f"\n  building profile from {total:.1f}s…")
    voiceprint.enrol(clips, sample_rate=TARGET_RATE)
    _report(voiceprint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
