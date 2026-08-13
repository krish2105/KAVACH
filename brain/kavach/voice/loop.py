"""The Phase 2 voice loop: trigger → STT → TTS.

Spec §9 is explicit that Phase 2 carries **no LLM** — "just echo/dictation,
get latency right first". So the reply is an echo. Phase 3 replaces exactly
one method (`respond`) with the router, and nothing else here moves.

Two triggers, same path:
  * **push-to-talk** — always available, needs no model
  * **wake word** — used when a trained model exists

Everything passes `KillSwitch.guard()` before it runs, and a kill mid-turn
cancels playback immediately (§7, §C).
"""

from __future__ import annotations

import asyncio
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..killswitch.core import KillSwitch, KillSwitchDisarmed
from ..reasoning import handlers as handlers_mod
from ..reasoning.router import Route, Router
from . import tts as tts_mod
from .latency import TurnTimer
from .mic import EndpointConfig, MicStream, Recorder, rms_to_amplitude
from . import stt as stt_mod
from . import vad
from .stt import SpeechToText
from .tts import TextToSpeech
from .wake import WakeWordDetector

log = logging.getLogger("kavach.voice.loop")

#: Spoken confirmations. Short on purpose — an assistant that narrates every
#: skip becomes something you turn off.
_MUSIC_ACK = {
    "pause": "Paused.",
    "play": "Playing.",
    "next": "Skipped.",
    "previous": "Going back.",
    "volume": "Done.",
    "volume_up": "Turning it up.",
    "volume_down": "Turning it down.",
    "play_item": "Playing that.",
}

DEFAULT_MODELS_DIR = Path(__file__).resolve().parents[2] / "models"

_WAKEWORD_DIR = Path(__file__).resolve().parents[2] / "wakeword" / "output"

#: livekit-wakeword exports to `<output_dir>/<model_name>/<model_name>.onnx`
#: — note the doubled name. The flatter path is checked too so a hand-placed
#: or hand-renamed model still works.
_WAKE_MODEL_CANDIDATES = (
    _WAKEWORD_DIR / "kavach" / "kavach.onnx",
    _WAKEWORD_DIR / "kavach.onnx",
)


def find_wake_model() -> Path:
    """First candidate that exists, else the canonical export path.

    Returning the canonical path when nothing exists keeps the "not trained
    yet" error message pointing somewhere useful.
    """
    for candidate in _WAKE_MODEL_CANDIDATES:
        if candidate.exists():
            return candidate
    return _WAKE_MODEL_CANDIDATES[0]


DEFAULT_WAKE_MODEL = find_wake_model()


@dataclass
class VoiceState:
    """Mirrors the TypeScript `KavachSnapshot` in apps/orb/lib/kavachState.ts.

    Keep the field names identical — the bridge serialises this straight to
    the orb, and a rename here silently breaks the HUD.
    """

    state: str = "idle"
    transcript: str = ""
    partial: str = ""
    amplitude: float = 0.0
    confidence: float = 1.0
    route: str | None = None
    toolCalls: list[dict] = field(default_factory=list)
    killSwitch: str = "armed"

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "transcript": self.transcript,
            "partial": self.partial,
            "amplitude": round(self.amplitude, 3),
            "confidence": round(self.confidence, 3),
            "route": self.route,
            "toolCalls": self.toolCalls,
            "killSwitch": self.killSwitch,
        }


Publisher = Callable[[dict], None]


class VoiceLoop:
    def __init__(
        self,
        kill_switch: KillSwitch,
        publish: Publisher | None = None,
        models_dir: Path = DEFAULT_MODELS_DIR,
        wake_model: Path = DEFAULT_WAKE_MODEL,
        stt_model: str = "large-v3-turbo",
        use_wake_word: bool = True,
        router: Router | None = None,
        local=None,
        agent=None,
        memory=None,
        voiceprint=None,
    ):
        self.ks = kill_switch
        self.publish_fn = publish or (lambda _: None)
        self.state = VoiceState()

        self.mic = MicStream()
        self.recorder = Recorder(self.mic, EndpointConfig())
        self.stt = SpeechToText(stt_model)
        self.tts = TextToSpeech(models_dir)
        self.wake = WakeWordDetector(wake_model) if use_wake_word else None
        # Phase 3 reasoning. All optional: with none of them the loop
        # degrades to the Phase 2 echo rather than failing.
        self.router = router
        self.local = local
        self.agent = agent
        # Optional: with no store the loop simply has no memory, rather
        # than failing. Turns are recorded only when one is wired.
        self.memory = memory
        # When enrolled, every turn must match this speaker before it is
        # transcribed. None (or not enrolled) means no speaker gating.
        self.voiceprint = voiceprint

        self._thread: threading.Thread | None = None
        self._running = False
        self._ptt = threading.Event()
        #: Set by a global hotkey; ends on silence rather than release.
        self._talk_requested = threading.Event()
        # Set by a held gesture from the orb while a confirmation is
        # pending. None means nobody has answered yet.
        self._gesture_answer: bool | None = None
        self._gesture_event = threading.Event()
        self.turns: list[dict] = []

    # ——— publishing ———

    def publish(self) -> None:
        self.state.killSwitch = "armed" if self.ks.is_armed else "disarmed"
        self.publish_fn(self.state.as_dict())

    def set_state(self, state: str, **fields) -> None:
        self.state.state = state
        for key, value in fields.items():
            setattr(self.state, key, value)
        self.publish()

    # ——— lifecycle ———

    def _wake_word_is_trustworthy(self) -> bool:
        """Only listen for the wake word if it has been calibrated on a real voice.

        The v1 model scored recall 0.982 on held-out clips from the same
        synthetic generator that produced its training set, then fired at 0.76
        on an empty room while the user's own "KAVACH" peaked at 0.57. An
        uncalibrated wake word does not sit idle — it starts turns, wakes the
        display, and sends room noise to the transcriber.
        
        `kavach-waketune` refuses to write a threshold unless it can actually
        separate the wake word from ordinary speech, so the presence of that
        file is exactly the evidence needed here.
        """
        from .waketune import load_calibration

        if load_calibration() is not None:
            return True
        log.warning(
            "wake word not calibrated — staying on push-to-talk. The model "
            "fires on room noise until `uv run kavach-waketune` can separate "
            "your voice from it."
        )
        return False

    def warm_up(self) -> None:
        """Load every model before the first turn.

        Deliberately eager: lazy loading charges several seconds to the user's
        first sentence, which is precisely where latency is most visible.
        """
        self.set_state("boot")
        self.stt.load()
        self.tts.load()
        if self.wake is not None:
            if self.wake.available and self._wake_word_is_trustworthy():
                self.wake.load()
            else:
                log.warning(
                    "no wake-word model at %s — push-to-talk only. Train with:\n"
                    "  uv run livekit-wakeword run wakeword/kavach.yaml",
                    self.wake.model_path,
                )
                self.wake = None
        self.set_state("idle")

    def start(self) -> None:
        self.mic.start()
        self._running = True
        self._thread = threading.Thread(target=self._run, name="kavach-voice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._thread is not None:
            self._thread.join(timeout=3)
        tts_mod.stop_playback()
        self.mic.stop()

    # ——— push to talk ———

    def push_to_talk(self, pressed: bool) -> None:
        if pressed:
            self._ptt.set()
        else:
            self._ptt.clear()

    def speak(self, text: str) -> None:
        """Synthesize and play, driving the orb's amplitude from the envelope.
        Shared with the confirmation flow so both sound identical."""
        if not text:
            return
        speech = self.tts.synthesize(text)
        self._play_with_envelope(speech)

    def on_tool_event(self, event: dict) -> None:
        """Feed real MCP tool calls to the orb (§4 #2).

        Phase 1 built the packet animation against mock events; these are the
        real ones, arriving on the same snapshot field.
        """
        if event.get("status") == "done":
            return

        call_id = event.get("id")
        existing = next((c for c in self.state.toolCalls if c["id"] == call_id), None)

        if existing is not None:
            existing["status"] = event.get("status", existing["status"])
            existing["endedAt"] = time.time() * 1000
        else:
            name = event.get("name", "")
            parts = name.split("__")
            self.state.toolCalls.insert(0, {
                "id": call_id or name,
                "server": parts[1] if len(parts) > 2 else "?",
                "tool": parts[2] if len(parts) > 2 else name,
                "summary": self._summarise_tool(event.get("input") or {}),
                "status": event.get("status", "pending"),
                "startedAt": time.time() * 1000,
            })
            del self.state.toolCalls[12:]
        self.publish()

    @staticmethod
    def _summarise_tool(args: dict) -> str:
        for value in args.values():
            if isinstance(value, str) and value.strip():
                return value.strip()[:110]
        return "no arguments"

    def answer_confirmation(self, approved: bool) -> None:
        """A held gesture answered the pending confirmation.

        Only meaningful while a confirmation is actually open; outside that
        window it is recorded and cleared, never acted on. A thumbs-up at the
        orb must not authorise something that was never asked.
        """
        self._gesture_answer = approved
        self._gesture_event.set()
        log.info("gesture answer: %s", "confirm" if approved else "deny")

    def take_gesture_answer(self) -> bool | None:
        answer, self._gesture_answer = self._gesture_answer, None
        self._gesture_event.clear()
        return answer

    def arm_gesture_answer(self) -> None:
        """Open the window. Clears anything stale so a gesture made before the
        question cannot answer it."""
        self._gesture_answer = None
        self._gesture_event.clear()

    def start_turn(self) -> None:
        """Begin a turn that ends on silence, with no key to hold.

        Push-to-talk needs the *page* to have keyboard focus, and the floating
        panel is deliberately non-activating — so Space could never reach it,
        and pressing it over the panel did nothing at all. A global hotkey in
        the presence process can always be heard; this is what it calls.
        """
        self._talk_requested.set()

    def interrupt(self) -> None:
        """Esc / spoken 'stop' — cut playback, return to idle, stay armed."""
        tts_mod.stop_playback()
        self.set_state("idle", partial="", amplitude=0.0)

    # ——— the loop ———

    def _run(self) -> None:
        while self._running:
            try:
                block = self.mic.read(timeout=0.5)
                if block is None:
                    continue

                if not self.ks.is_armed:
                    # Latched: stop listening entirely and show it.
                    if self.state.state != "halted":
                        tts_mod.stop_playback()
                        self.set_state("halted", partial="", amplitude=0.0)
                        self.mic.forget()
                    continue

                if self.state.state == "halted":
                    self.set_state("idle")

                triggered = False
                # A global hotkey asked for a turn. Same path as the wake
                # word, so it ends on silence rather than on a key release —
                # there is no key to release.
                if self._talk_requested.is_set():
                    self._talk_requested.clear()
                    triggered = True
                elif self._ptt.is_set():
                    triggered = True
                elif self.wake is not None:
                    if self.wake.push(block) is not None:
                        triggered = True

                if triggered:
                    self._handle_turn()
                    if self.wake is not None:
                        self.wake.reset()
                    # Audio that was not acted on must not linger (§7).
                    self.mic.forget()

            except Exception:
                log.exception("voice loop error; continuing")
                self.set_state("idle")

    def _handle_turn(self) -> None:
        timer = TurnTimer()
        try:
            self.ks.guard("voice turn")
        except KillSwitchDisarmed:
            self.set_state("halted")
            return

        # ——— listen ———
        self.set_state("listening", transcript="", partial="")
        timer.start("record")
        audio = self._record_with_meter()
        record_ms = timer.stop("record")

        if len(audio) < 16_000 * 0.3:  # under 300 ms is a cough, not a command
            self.set_state("idle", amplitude=0.0)
            return

        # Did a human actually speak? Whisper does not return empty for
        # non-speech, it confabulates — an empty room produced "Legend and
        # legend do it." and "The problem was that the case was not coming."
        # From Phase 4 those strings reach a router that can act on them.
        #
        # Energy alone was not enough: the gate sat at rms 0.006 and this room
        # measures 0.0365, so room tone sailed through. This asks whether the
        # audio is *voiced*, which loudness cannot answer.
        if not vad.has_speech(audio, 16_000):
            log.info("no speech in the clip (%s), discarding turn",
                     vad.describe(audio, 16_000))
            self.set_state("idle", amplitude=0.0)
            return

        # And is it *you*?
        #
        # VAD is necessary but not sufficient: measured in this room, ambient
        # sound scores 142/152 voiced frames with a tonal spectrum, so it
        # passes every acoustic test while containing no speech at all. The
        # voiceprint is the only gate that held — imposter voices score
        # 0.33-0.41 against a 0.673 threshold.
        #
        # The cost is deliberate: KAVACH answers to one person. An assistant
        # that acts on whatever the room says is the failure §7 exists to stop.
        if self.voiceprint is not None and self.voiceprint.is_enrolled:
            result = self.voiceprint.verify(audio, sample_rate=16_000)
            if not result.accepted:
                log.info("voice did not match the enrolled speaker "
                         "(similarity %.3f < %.3f), discarding turn",
                         result.similarity, result.threshold)
                # VerificationResult.as_dict() already carries `reason`, so
                # passing one alongside it raised TypeError and killed the turn.
                self.ks.log.append("voice.rejected", **result.as_dict())
                self.set_state("idle", amplitude=0.0)
                return

        try:
            self.ks.guard("transcribe")
        except KillSwitchDisarmed:
            self.set_state("halted")
            return

        # ——— transcribe ———
        self.set_state("thinking", amplitude=0.0)
        timer.start("stt")
        result = self.stt.transcribe(audio)
        stt_ms = timer.stop("stt")

        if not result.text:
            log.info("empty transcript, discarding turn")
            self.set_state("idle")
            return

        self.set_state("thinking", transcript=result.text, partial="")

        # ——— respond ———
        timer.start("respond")
        reply = self.respond(result.text)
        respond_ms = timer.stop("respond")

        try:
            self.ks.guard("speak")
        except KillSwitchDisarmed:
            self.set_state("halted")
            return

        # ——— speak ———
        timer.start("tts")
        # Reply in whatever language was spoken, when Kokoro has a voice
        # for it. Falls back to English rather than guessing.
        speech = self.tts.synthesize(reply, language=result.language)
        tts_ms = timer.stop("tts")

        self.set_state("speaking", transcript=reply)
        timer.start("playback")
        self._play_with_envelope(speech)
        timer.stop("playback")

        record = {
            "heard": result.text,
            "said": reply,
            "record_ms": round(record_ms),
            "stt_ms": round(stt_ms),
            "respond_ms": round(respond_ms),
            "tts_ms": round(tts_ms),
            # What the user actually experiences: silence → first audio out.
            "perceived_ms": round(stt_ms + respond_ms + tts_ms),
            "language": result.language,
        }
        self.turns.append(record)
        self.ks.log.append("voice.turn", **record)

        # Remember the turn so later questions can refer back to it.
        # Failure here must never break a turn that already succeeded.
        if self.memory is not None:
            try:
                self.memory.remember(
                    f"User said: {result.text}\nKAVACH replied: {reply}",
                    collection="turns",
                )
            except Exception:
                log.debug("could not store turn in memory", exc_info=True)
        log.info(
            "turn: stt=%dms respond=%dms tts=%dms → perceived %dms",
            record["stt_ms"], record["respond_ms"], record["tts_ms"],
            record["perceived_ms"],
        )

        self.set_state("idle", amplitude=0.0, partial="")

    def respond(self, text: str) -> str:
        """Route the utterance and produce a spoken reply (spec §5).

        Phase 2 echoed. Phase 3 replaces exactly this method — the rest of the
        loop is unchanged, which is what the Phase 2 seam was for.
        """
        if self.router is None:
            return f"You said: {text}"

        decision = self.router.route(text)
        # Confidence drives the orb's outer shell (§4 #3).
        self.state.confidence = decision.confidence
        self.state.route = (
            decision.route.value if decision.route.value != "reject" else None
        )
        self.ks.log.append("router.decision", **decision.as_dict())

        if decision.route is Route.REJECT:
            return ""

        # §7: destructive or externally visible actions are spoken back and
        # wait. Phase 3 has no tools, so nothing could execute yet — but the
        # confirmation habit is established here rather than bolted on later.
        if decision.needs_confirmation:
            return (
                f"That would {self._describe_action(text)}. "
                f"Say confirm if you want me to."
            )

        # Music, before anything else. "skip this" should not wait on a
        # language model, and the parser returns None for anything it does not
        # clearly recognise, so it cannot swallow unrelated requests.
        music_reply = self._handle_music(text)
        if music_reply is not None:
            self.state.confidence = 0.99
            return music_reply

        # Cheapest tier first: a deterministic handler is sub-millisecond
        # and, for things like the clock, the only tier that can actually
        # answer at all.
        if decision.route is Route.LOCAL and decision.intent:
            answer = handlers_mod.handle(decision.intent, text)
            if answer:
                self.state.confidence = 0.98
                return answer

        try:
            if decision.route is Route.LOCAL and self.local is not None:
                return self.local.respond(text)
            if self.agent is not None:
                return asyncio.run(
                    self.agent.respond(text, on_tool=self.on_tool_event)
                )
        except Exception:
            log.exception("reasoning failed")
            return "Something went wrong while I was thinking about that."

        return f"You said: {text}"

    def _handle_music(self, text: str) -> str | None:
        """Answer a music request, or None if it was not one.

        Runs against whichever player is already open. Starting a music app
        because someone said "skip this" would be a surprise; controlling the
        one already playing is what was meant.
        """
        from ..reasoning import music as music_mod

        command = music_mod.parse_music_command(text)
        if command is None:
            return None

        player = music_mod.running_player()
        if player is None:
            for candidate in (music_mod.Player.SPOTIFY, music_mod.Player.MUSIC):
                if music_mod.installed(candidate):
                    player = candidate
                    break
        if player is None:
            return "I can't find a music app to control."

        script = music_mod.build_script(command, player)
        try:
            result = subprocess.run(
                ["osascript", "-e", script],
                capture_output=True, text=True, timeout=10,
            )
        except Exception:
            log.exception("music command failed")
            return "That didn't work."

        self.ks.log.append(
            "music.command",
            action=command.action.value,
            player=player.value,
            query=command.query,
        )

        if result.returncode != 0:
            detail = (result.stderr or "").strip().splitlines()
            log.warning("music: %s", detail[-1] if detail else "failed")
            return f"{player.value} didn't accept that."

        out = (result.stdout or "").strip()
        if command.action is music_mod.MusicAction.NOW_PLAYING:
            if not out or out == "nothing":
                return "Nothing is playing."
            return f"{out}."
        if out == "not found":
            return f"I couldn't find {command.query} in your library."
        return _MUSIC_ACK.get(command.action.value, "Done.")

    @staticmethod
    def _describe_action(text: str) -> str:
        """A short spoken paraphrase for the confirmation prompt."""
        trimmed = text.strip().rstrip(".")
        return trimmed[0].lower() + trimmed[1:] if trimmed else "do that"

    # ——— helpers that keep the orb alive during a turn ———

    def _record_with_meter(self) -> np.ndarray:
        """Record, publishing amplitude as we go so the orb pulses live."""
        cfg = self.recorder.config
        # Remember how this turn started: a push-to-talk turn ends on key
        # release, a wake-word turn ends on silence.
        ptt_turn = self._ptt.is_set()
        chunks: list[np.ndarray] = []
        pre = self.mic.preroll(500)
        if len(pre):
            chunks.append(pre)

        started = time.monotonic()
        silence_started: float | None = None

        while self._running:
            block = self.mic.read(timeout=1.0)
            if block is None:
                if (time.monotonic() - started) * 1000 > cfg.max_utterance_ms:
                    break
                continue
            if not self.ks.is_armed:
                break

            chunks.append(block)
            self.state.amplitude = rms_to_amplitude(block)
            self.publish()

            elapsed_ms = (time.monotonic() - started) * 1000
            rms = float(np.sqrt(np.mean(block**2)))

            if rms < cfg.silence_rms:
                if silence_started is None:
                    silence_started = time.monotonic()
                elif (
                    (time.monotonic() - silence_started) * 1000 >= cfg.silence_ms
                    and elapsed_ms >= cfg.min_utterance_ms
                ):
                    break
            else:
                silence_started = None

            # Releasing push-to-talk ends the utterance at once — waiting for
            # the silence timeout after the key is up feels broken.
            if ptt_turn and not self._ptt.is_set() and elapsed_ms >= cfg.min_utterance_ms:
                break

            if elapsed_ms >= cfg.max_utterance_ms:
                break

        return np.concatenate(chunks) if chunks else np.zeros(0, dtype=np.float32)

    def _play_with_envelope(self, speech: tts_mod.Speech) -> None:
        """Play, stepping the orb's amplitude along the TTS envelope."""
        env = tts_mod.envelope(speech.audio, speech.sample_rate)
        duration = len(speech.audio) / speech.sample_rate

        tts_mod.play(speech, blocking=False)

        started = time.monotonic()
        i = 0
        while time.monotonic() - started < duration:
            if not self.ks.is_armed:
                tts_mod.stop_playback()
                self.set_state("halted", amplitude=0.0)
                return
            self.state.amplitude = env[i] if i < len(env) else 0.0
            self.publish()
            i += 1
            time.sleep(0.04)

        self.state.amplitude = 0.0
        self.publish()
