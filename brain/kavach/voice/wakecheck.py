"""`kavach-wakecheck` — say the wake word and see exactly what KAVACH heard.

    uv run kavach-wakecheck

The wake word did not fire and there was **no way to find out why**. §7 says
a burst that was not acted on leaves no trace, so the daemon never logs the
transcript of a non-matching burst — correctly, and it means "it didn't
work" is the whole of the available evidence.

This is the opt-in exception, and the same shape `kavach-waketune` has for
the ONNX path: **you run it, it prints, it stores nothing.** Nothing is
written to disk, nothing enters the action log, and it exits when you stop
it. Diagnosis you asked for is not surveillance; the difference is entirely
that you typed the command.

What it shows, per burst of speech:

    heard   'go watch.'                    ✓ WAKE
    heard   'coverage'                     ✗ no match   closest: kavatch 0.55

The near-miss is the useful part. Every spelling in `WAKE_TARGETS` was
found this way — this microphone renders कवच as *gavaj*, *gauj*, *vajah* and
*go watch*, and none of that is guessable. If your voice produces a spelling
that is close and not listed, that is the fix, and it is a one-line one.
"""

from __future__ import annotations

import argparse
import sys
import time
from difflib import SequenceMatcher

import numpy as np

from .wakewhisper import (
    MATCH_RATIO,
    WAKE_TARGETS,
    WhisperWakeDetector,
    _WORD_RE,
    matches_wake,
)


def closest_target(text: str) -> tuple[str, str, float]:
    """The word in `text` that came nearest to waking it, and how near.

    Reported because "no match" alone cannot distinguish *the model did not
    hear the word* from *the model heard it and spelled it unusually*, and
    those have completely different fixes.
    """
    best = ("", "", 0.0)
    for word in _WORD_RE.findall((text or "").lower()):
        if len(word) < 4:
            continue
        for target in WAKE_TARGETS:
            ratio = SequenceMatcher(None, word, target).ratio()
            if ratio > best[2]:
                best = (word, target, ratio)
    return best


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="kavach-wakecheck",
        description="Say the wake word; see what whisper wrote. Stores nothing.",
    )
    parser.add_argument("--seconds", type=int, default=60,
                        help="how long to listen (default 60)")
    parser.add_argument("--model", default=None,
                        help="override the wake-word whisper model")
    args = parser.parse_args(argv)

    from .mic import MicStream

    detector = WhisperWakeDetector(model=args.model)
    print(f"\n  loading {detector.model} …", flush=True)
    detector.load()

    print(f"""
  Say "Kavach, what time is it?" a few times, normally.

  Nothing here is saved — no file, no log, no action record. It prints and
  forgets, and stops on its own after {args.seconds}s (Ctrl-C to stop sooner).
""", flush=True)

    heard = 0
    woke = 0
    started = time.monotonic()

    # start()/stop(), not a context manager — `with MicStream()` raises
    # AttributeError, which would have been the third broken command handed
    # over in this session. Checked against mic.py rather than assumed.
    mic = MicStream()
    try:
        mic.start()
        while time.monotonic() - started < args.seconds:
            block = mic.read(timeout=0.5)
            if block is None:
                continue
            burst = detector._segmenter.push(np.asarray(block, np.float32))
            if burst is None:
                continue

            heard += 1
            seconds = len(burst) / 16_000
            text = (detector.stt.transcribe(burst, language="en").text
                    or "").strip()

            if matches_wake(text):
                woke += 1
                print(f"  heard   {text!r:44} ✓ WAKE  ({seconds:.1f}s)")
            else:
                word, target, ratio = closest_target(text)
                near = (f"closest: {word!r}→{target!r} {ratio:.2f}"
                        if word else "no word close to it")
                print(f"  heard   {text!r:44} ✗ {near}  ({seconds:.1f}s)")
    except KeyboardInterrupt:
        print("\n  stopped.")
    except Exception as exc:
        print(f"\n✗ could not listen: {exc}", file=sys.stderr)
        return 1
    finally:
        try:
            mic.stop()
        except Exception:
            pass

    print(f"\n  {woke}/{heard} burst(s) woke it.")
    if heard == 0:
        print("  Nothing reached the segmenter at all — that is a microphone\n"
              "  or a level problem, not a wake-word one.")
    elif woke == 0:
        print("  It heard you and did not match. If a spelling above is close,\n"
              "  add it to WAKE_TARGETS in kavach/voice/wakewhisper.py —\n"
              "  that is how gavaj, gauj and vajah got there.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
