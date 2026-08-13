"""Run the Phase 2 voice loop.

    uv run python -m kavach.voice                 # wake word if trained, else PTT
    uv run python -m kavach.voice --no-wake-word  # push-to-talk only
    uv run python -m kavach.voice --bench "hello" # measure latency, no mic

Serves the bridge on ws://127.0.0.1:8765 so the orb shows what it hears.
The kill switch runs alongside on its usual socket — ⌃⌥⌘K still stops
everything, including playback mid-sentence.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
from pathlib import Path

from ..bridge.server import DEFAULT_HOST, DEFAULT_PORT, Bridge
from ..killswitch.core import KillSwitch
from ..killswitch.ipc import DEFAULT_SOCKET_PATH, serve as serve_killswitch
from ..killswitch.log import ActionLog
from .loop import DEFAULT_MODELS_DIR, DEFAULT_WAKE_MODEL, VoiceLoop


def _bench(loop: VoiceLoop, phrase: str, rounds: int) -> int:
    """Latency without a microphone: synthesize the phrase, transcribe that
    audio, and time the round trip. Isolates model speed from mic behaviour."""
    import numpy as np

    from .latency import TurnTimer

    print(f"\nBenchmarking {rounds} round(s) on: {phrase!r}\n")
    loop.warm_up()

    for i in range(1, rounds + 1):
        timer = TurnTimer()

        timer.start("tts_in")
        spoken = loop.tts.synthesize(phrase)
        tts_in = timer.stop("tts_in")

        # Kokoro is 24 kHz, Whisper wants 16 kHz — decimate by 3 after
        # upsampling by 2 is overkill here; simple resample is enough for a
        # benchmark signal.
        audio = spoken.audio
        idx = (np.arange(int(len(audio) * 16000 / spoken.sample_rate))
               * spoken.sample_rate / 16000).astype(int)
        audio16 = audio[np.clip(idx, 0, len(audio) - 1)].astype(np.float32)

        timer.start("stt")
        result = loop.stt.transcribe(audio16)
        stt = timer.stop("stt")

        timer.start("respond")
        reply = loop.respond(result.text)
        respond = timer.stop("respond")

        timer.start("tts_out")
        loop.tts.synthesize(reply)
        tts_out = timer.stop("tts_out")

        note = "  (cold: includes warm-up)" if i == 1 else ""
        print(f"  run {i}{note}")
        print(f"    heard back : {result.text!r}")
        print(f"    stt        : {stt:7.0f} ms")
        print(f"    respond    : {respond:7.0f} ms")
        print(f"    tts        : {tts_out:7.0f} ms")
        print(f"    PERCEIVED  : {stt + respond + tts_out:7.0f} ms  "
              f"(silence → first audio out)")
        print(f"    [tts to make the test signal: {tts_in:.0f} ms]\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kavach-voice")
    parser.add_argument("--no-wake-word", action="store_true",
                        help="push-to-talk only; skip the wake-word model")
    parser.add_argument("--wake-model", default=str(DEFAULT_WAKE_MODEL))
    parser.add_argument("--models-dir", default=str(DEFAULT_MODELS_DIR))
    parser.add_argument("--stt-model", default="large-v3-turbo")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--bench", metavar="PHRASE", default=None,
                        help="measure latency without a mic, then exit")
    parser.add_argument("--bench-rounds", type=int, default=3)
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    ks = KillSwitch(log=ActionLog())
    voice = VoiceLoop(
        kill_switch=ks,
        models_dir=Path(args.models_dir),
        wake_model=Path(args.wake_model),
        stt_model=args.stt_model,
        use_wake_word=not args.no_wake_word,
    )

    if args.bench:
        return _bench(voice, args.bench, args.bench_rounds)

    bridge = Bridge(voice, ks)
    voice.publish_fn = bridge.publish

    voice.warm_up()
    voice.start()

    trigger = "wake word 'KAVACH' or hold Space" if voice.wake else "hold Space (push-to-talk)"
    print("─" * 62)
    print("  KAVACH voice loop — Phase 2 (echo, no LLM yet)")
    print("─" * 62)
    print(f"  trigger    {trigger}")
    print(f"  stt        {args.stt_model}")
    print(f"  bridge     ws://{args.host}:{args.port}  → the orb")
    print(f"  kill       ⌃⌥⌘K, menu bar, or `kavach kill`")
    print("─" * 62)
    sys.stdout.flush()

    async def run_all() -> None:
        # The kill-switch socket runs here too, so `kavach kill` halts the
        # voice loop mid-sentence without a second daemon.
        await serve_killswitch(ks, DEFAULT_SOCKET_PATH)
        await bridge.run(args.host, args.port)

    try:
        asyncio.run(run_all())
    except KeyboardInterrupt:
        print("\nstopping…")
    finally:
        voice.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
