"""Voice-loop tests.

The audio path itself needs a microphone and a human, so it can't be asserted
here. What *can* be pinned down is everything around it — and the most
valuable test in this file is the contract one: the Python snapshot and the
TypeScript `KavachSnapshot` must not drift apart, because nothing at runtime
would tell us if they did. The orb would just quietly stop updating a field.
"""

import re
from pathlib import Path

import numpy as np
import pytest

from kavach.killswitch.core import KillSwitch, KillSwitchDisarmed
from kavach.killswitch.log import ActionLog
from kavach.voice.latency import TurnTimer
from kavach.voice.loop import VoiceState
from kavach.voice.mic import EndpointConfig, rms_to_amplitude
from kavach.voice.wake import WakeWordDetector

ORB_STATE_TS = (
    Path(__file__).resolve().parents[2] / "apps" / "orb" / "lib" / "kavachState.ts"
)


# ——— the contract with the Presence layer ———

def test_snapshot_fields_match_the_typescript_interface():
    """VoiceState.as_dict() feeds the orb directly over the bridge.

    A field renamed on one side and not the other fails silently at runtime:
    the HUD keeps rendering, just with a stale value forever. Cheaper to catch
    it here.
    """
    source = ORB_STATE_TS.read_text()
    block = re.search(
        r"export interface KavachSnapshot \{(.*?)\n\}", source, re.S
    )
    assert block, "could not find KavachSnapshot in kavachState.ts"

    ts_fields = set(re.findall(r"^\s*(\w+)\??:", block.group(1), re.M))
    py_fields = set(VoiceState().as_dict())

    assert py_fields == ts_fields, (
        f"snapshot drift — only in Python: {py_fields - ts_fields}; "
        f"only in TypeScript: {ts_fields - py_fields}"
    )


def test_snapshot_states_are_all_known_to_the_orb():
    """Every state the loop can publish must have an OrbState counterpart."""
    source = ORB_STATE_TS.read_text()
    orb_scene = (ORB_STATE_TS.parent / "orbScene.ts").read_text()
    block = re.search(r"export type OrbState =(.*?);", orb_scene, re.S)
    assert block, "could not find OrbState in orbScene.ts"
    orb_states = set(re.findall(r'"(\w[\w-]*)"', block.group(1)))

    published = {"boot", "idle", "listening", "thinking", "speaking", "halted"}
    assert published <= orb_states, f"orb cannot render: {published - orb_states}"
    assert "KavachSnapshot" in source


# ——— amplitude mapping ———

@pytest.mark.parametrize("level", [0.0, 0.01, 0.05, 0.2, 0.5, 1.0])
def test_amplitude_stays_in_range(level):
    block = np.full(512, level, dtype=np.float32)
    value = rms_to_amplitude(block)
    assert 0.0 <= value <= 1.0


def test_amplitude_is_zero_for_silence_and_empty():
    assert rms_to_amplitude(np.zeros(512, dtype=np.float32)) == 0.0
    assert rms_to_amplitude(np.zeros(0, dtype=np.float32)) == 0.0


def test_amplitude_is_monotonic_in_loudness():
    quiet = rms_to_amplitude(np.full(512, 0.02, dtype=np.float32))
    normal = rms_to_amplitude(np.full(512, 0.10, dtype=np.float32))
    loud = rms_to_amplitude(np.full(512, 0.30, dtype=np.float32))
    assert quiet < normal < loud


def test_quiet_speech_still_moves_the_orb():
    """A linear RMS→amplitude map leaves normal speech barely visible; the
    sqrt curve exists so the orb actually reacts."""
    assert rms_to_amplitude(np.full(512, 0.03, dtype=np.float32)) > 0.25


# ——— endpointing config sanity ———

def test_endpoint_config_is_internally_consistent():
    cfg = EndpointConfig()
    assert cfg.min_utterance_ms < cfg.max_utterance_ms
    assert cfg.silence_ms < cfg.max_utterance_ms
    assert 0 < cfg.silence_rms < 0.1


# ——— latency instrumentation ———

def test_turn_timer_records_named_spans():
    timer = TurnTimer()
    timer.start("stt")
    timer.stop("stt")
    timer.start("tts")
    timer.stop("tts")

    recorded = timer.as_dict()
    assert {"stt", "tts", "total"} <= set(recorded)
    assert all(value >= 0 for value in recorded.values())


def test_turn_timer_stop_without_start_is_harmless():
    """The loop has early-return paths that skip stages; a missing stop must
    not raise on top of whatever already went wrong."""
    assert TurnTimer().stop("never-started") == 0.0


# ——— wake word ———

def test_missing_wake_model_reports_how_to_train_one(tmp_path):
    detector = WakeWordDetector(tmp_path / "nope.onnx")
    assert not detector.available
    with pytest.raises(FileNotFoundError, match="livekit-wakeword run"):
        detector.load()


# ——— the guardrail still holds in the voice path (§7) ———

def test_kill_switch_blocks_a_voice_turn(tmp_path):
    ks = KillSwitch(log=ActionLog(tmp_path / "a.jsonl"))
    ks.guard("voice turn")  # armed: fine

    ks.trigger(source="test", reason="mid-turn")

    with pytest.raises(KillSwitchDisarmed):
        ks.guard("voice turn")
    with pytest.raises(KillSwitchDisarmed):
        ks.guard("speak")


def test_voice_state_reports_kill_switch_to_the_orb(tmp_path):
    """The orb's DISARMED badge is driven by this field, so it has to reflect
    the real latch rather than a UI-local flag."""
    state = VoiceState()
    assert state.as_dict()["killSwitch"] == "armed"
    state.killSwitch = "disarmed"
    assert state.as_dict()["killSwitch"] == "disarmed"


# ——— silence must not become a command (§7) ———

def test_known_silence_hallucinations_are_rejected():
    """Whisper confabulates on silence rather than returning nothing. Observed
    live: near-silent audio transcribed as 'Thank you.' From Phase 4 these
    strings reach a router that can act on them."""
    from kavach.voice.stt import is_hallucination

    for text in ["Thank you.", "thank you", "Thanks for watching!", "you", "Bye."]:
        assert is_hallucination(text), text


def test_real_commands_are_not_rejected_as_hallucinations():
    from kavach.voice.stt import is_hallucination

    for text in ["open Safari", "what's on my calendar tomorrow",
                 "thank you for opening Safari", "delete the draft"]:
        assert not is_hallucination(text), text


def test_silence_is_gated_before_transcription():
    """Cheaper and more reliable than filtering Whisper's output afterwards."""
    from kavach.voice.stt import is_probably_silence

    assert is_probably_silence(np.zeros(16000, dtype=np.float32))
    assert is_probably_silence(np.zeros(0, dtype=np.float32))
    # Room tone: audible to a meter, not speech.
    assert is_probably_silence(np.random.normal(0, 0.001, 16000).astype(np.float32))


def test_actual_speech_passes_the_gate():
    from kavach.voice.stt import is_probably_silence

    speech = (np.sin(np.linspace(0, 400 * np.pi, 16000)) * 0.15).astype(np.float32)
    assert not is_probably_silence(speech)
