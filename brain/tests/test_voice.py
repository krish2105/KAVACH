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


# ——— the wake model must be found where training actually writes it ———

def test_wake_model_is_looked_for_where_training_exports_it():
    """livekit-wakeword exports to <output_dir>/<name>/<name>.onnx — the name
    is doubled. An earlier default pointed one directory too high, which would
    have meant hours of training finishing and the loop quietly reporting
    "no wake-word model, push-to-talk only"."""
    from kavach.voice.loop import _WAKE_MODEL_CANDIDATES

    from livekit.wakeword.config import load_config

    config = load_config(
        str(Path(__file__).resolve().parents[1] / "wakeword" / "kavach.yaml")
    )
    exported = (Path(__file__).resolve().parents[1] / config.model_output_dir
                / f"{config.model_name}.onnx").resolve()

    candidates = {c.resolve() for c in _WAKE_MODEL_CANDIDATES}
    assert exported in candidates, (
        f"training exports to {exported}, which the loop never checks: {candidates}"
    )


def test_missing_wake_model_still_reports_a_useful_path():
    from kavach.voice.loop import find_wake_model

    assert str(find_wake_model()).endswith(".onnx")


def test_wake_threshold_never_drops_below_the_runtime_floor():
    """The trained optimum is not the runtime threshold.

    This test previously asserted `threshold == trained_threshold(model)`,
    on the reasoning that training measured 0.18 as optimal and a higher
    default would make the detector needlessly deaf.

    Measured on this machine, that reasoning was wrong. 0.18 is optimal
    *against the training negatives*, which score ~0.004 because they came
    from the same synthetic generator as the positives. Against audio the
    model never saw:

        digital silence                     0.705
        synthetic "what time is it"         0.917
        the user's real room, loudest 2 s   0.516

    Every one of those clears 0.18. So the runtime applies a floor, and
    prefers a threshold calibrated on the real user's voice when one exists
    (`kavach-waketune`) over any number derived from synthetic audio.
    """
    from kavach.voice.loop import find_wake_model
    from kavach.voice.wake import DEFAULT_THRESHOLD, WakeWordDetector, trained_threshold

    model = find_wake_model()
    if not model.exists():
        import pytest
        pytest.skip("wake word not trained on this machine")

    measured = trained_threshold(model)
    assert 0.0 < measured < 1.0

    resolved = WakeWordDetector(model).threshold
    assert resolved >= DEFAULT_THRESHOLD, "runtime floor must not be undercut"
    # An explicit threshold is still honoured — tuning tools need to pass 0.0.
    assert WakeWordDetector(model, threshold=0.4).threshold == 0.4


def test_calibration_measured_on_a_real_voice_wins():
    """A threshold measured on the user beats one derived from synthetic TTS."""
    import json
    from unittest.mock import patch

    from kavach.voice.loop import find_wake_model
    from kavach.voice.wake import WakeWordDetector

    model = find_wake_model()
    if not model.exists():
        import pytest
        pytest.skip("wake word not trained on this machine")

    with patch("kavach.voice.waketune.load_calibration", return_value=0.42):
        assert WakeWordDetector(model).threshold == 0.42


def test_threshold_falls_back_when_metrics_are_missing(tmp_path):
    from kavach.voice.wake import DEFAULT_THRESHOLD, trained_threshold

    assert trained_threshold(tmp_path / "nope.onnx") == DEFAULT_THRESHOLD


# ——— multilingual replies ———

def test_a_voice_is_chosen_for_each_supported_language():
    from kavach.voice.languages import VOICES, voice_for

    for code, expected in VOICES.items():
        assert voice_for(code) is expected


def test_locale_tags_resolve_to_their_base_language():
    """Whisper sometimes reports 'en-GB' or 'pt_BR'."""
    from kavach.voice.languages import voice_for

    assert voice_for("en-GB").espeak == "en-us"
    assert voice_for("pt_BR").name == "Portuguese"


def test_an_unsupported_language_falls_back_to_english():
    """A reply spoken in the WRONG language is worse than one in the default,
    so unmapped codes fall back rather than picking something adjacent."""
    from kavach.voice.languages import voice_for

    for code in ["sw", "is", "xx", "", None]:
        assert voice_for(code).name == "English", code


def test_voice_and_espeak_code_always_change_together():
    """The phonemiser needs the right language: a Hindi sentence with an
    English espeak code comes out as English phonemes read aloud."""
    from kavach.voice.languages import VOICES

    for code, lv in VOICES.items():
        assert lv.voice and lv.espeak, code
        if code != "en":
            assert lv.espeak != "en-us", f"{code} would be phonemised as English"


def test_transcript_carries_the_detected_language():
    from kavach.voice.stt import Transcript

    t = Transcript(text="bonjour", segments=1, model="large-v3-turbo", language="fr")
    assert t.language == "fr"
    # Defaults to None so existing callers are unaffected.
    assert Transcript(text="hi", segments=1, model="m").language is None


# ═══ a calibration belongs to the model it was measured on ═══
#
# Found when v2 finished training: `find_wake_model()` only looked for v1, and
# a Calibration recorded no model identity at all. Thresholds are model
# specific — v1's optimum was 0.70, v2's is 0.20 — so applying one model's
# threshold to another is not a small error, it is the difference between
# "never fires" and "fires at everything". Silent, too: nothing would have
# reported a mismatch.

def test_a_calibration_records_which_model_it_measured(tmp_path, monkeypatch):
    from kavach.voice import waketune

    model = tmp_path / "kavach_v2.onnx"
    model.write_bytes(b"fake onnx")
    monkeypatch.setattr(waketune, "CALIBRATION_PATH", tmp_path / "cal.json")

    cal = waketune.Calibration(threshold=0.2, positives=[0.9], negatives=[0.1],
                               separated=True, margin=0.8)
    waketune.save_calibration(cal, model=model)

    import json
    saved = json.loads((tmp_path / "cal.json").read_text())
    assert "model" in saved, "a threshold with no model is not usable"


def test_a_calibration_is_refused_for_a_different_model(tmp_path, monkeypatch):
    """The silent-failure case: right file, wrong model."""
    from kavach.voice import waketune

    v1 = tmp_path / "kavach.onnx"; v1.write_bytes(b"model one")
    v2 = tmp_path / "kavach_v2.onnx"; v2.write_bytes(b"model two, different")
    monkeypatch.setattr(waketune, "CALIBRATION_PATH", tmp_path / "cal.json")

    waketune.save_calibration(
        waketune.Calibration(threshold=0.2, positives=[0.9], negatives=[0.1],
                             separated=True, margin=0.8),
        model=v1,
    )

    assert waketune.load_calibration(model=v1) == pytest.approx(0.2)
    assert waketune.load_calibration(model=v2) is None, \
        "v1's threshold was applied to v2"


def test_a_changed_model_invalidates_its_calibration(tmp_path, monkeypatch):
    """Retraining in place must not silently inherit the old threshold."""
    from kavach.voice import waketune

    model = tmp_path / "kavach_v2.onnx"
    model.write_bytes(b"first training")
    monkeypatch.setattr(waketune, "CALIBRATION_PATH", tmp_path / "cal.json")
    waketune.save_calibration(
        waketune.Calibration(threshold=0.2, positives=[0.9], negatives=[0.1],
                             separated=True, margin=0.8),
        model=model,
    )
    assert waketune.load_calibration(model=model) == pytest.approx(0.2)

    model.write_bytes(b"retrained, different weights entirely")
    assert waketune.load_calibration(model=model) is None


def test_the_newest_trained_model_is_found(tmp_path, monkeypatch):
    """v2 existing must not leave the loop silently using v1."""
    from kavach.voice import loop as loop_mod

    (tmp_path / "kavach").mkdir()
    (tmp_path / "kavach" / "kavach.onnx").write_bytes(b"v1")
    (tmp_path / "kavach_v2").mkdir()
    (tmp_path / "kavach_v2" / "kavach_v2.onnx").write_bytes(b"v2")

    monkeypatch.setattr(loop_mod, "_WAKEWORD_DIR", tmp_path)
    found = loop_mod.find_wake_model()
    assert found.name == "kavach_v2.onnx", f"found {found} instead of v2"


# ═══ Explainability (Phase 13) ═══
#
# The raw material already existed and was already logged: RoutingDecision
# carries route, confidence, reason and intent, and every decision goes to the
# action log. It simply never reached the HUD — the snapshot had no field for
# it, so the one place you actually look could not tell you why KAVACH chose
# the path it chose.
#
# Honest scope: `reason` is the router's own short explanation of the routing
# decision, not a model-generated rationale about the answer. It says why this
# path was taken, which is what the phase asks for.

def test_the_snapshot_carries_the_routing_reason():
    from kavach.voice.loop import VoiceState

    fields = VoiceState().as_dict()
    assert "reason" in fields
    assert "intent" in fields


def test_reason_and_intent_match_the_typescript_contract():
    """Same drift guard the other fields have.

    VoiceState.as_dict() is serialised straight to the orb, so a field that
    exists on one side and not the other fails silently — the HUD just renders
    nothing and nobody notices.
    """
    from pathlib import Path

    from kavach.voice.loop import VoiceState

    ts = (Path(__file__).resolve().parents[2] / "apps" / "orb" / "lib"
          / "kavachState.ts").read_text()
    block = ts.split("interface KavachSnapshot")[1].split("}")[0]

    for field in ("reason", "intent"):
        assert field in block, f"{field} is missing from KavachSnapshot"


def test_a_routed_turn_puts_its_reason_in_the_snapshot():
    from tests.test_api import StubRouter, make_loop
    from kavach.api.confirm import PendingRegistry
    from kavach.killswitch.core import KillSwitch
    from kavach.killswitch.log import ActionLog
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as tmp:
        ks = KillSwitch(log=ActionLog(Path(tmp) / "a.jsonl"))
        loop = make_loop(PendingRegistry(), ks, router=StubRouter())
        loop.respond("what time is it")

        snapshot = loop.state.as_dict()
        assert snapshot["reason"] == "stub"
        assert snapshot["intent"] == "stub"


def test_a_rejected_turn_does_not_leak_a_reason():
    """A rejected utterance produces no reply, so it must not leave a stale
    explanation sitting in the HUD from the previous turn either."""
    from tests.test_api import make_loop
    from kavach.api.confirm import PendingRegistry
    from kavach.killswitch.core import KillSwitch
    from kavach.killswitch.log import ActionLog
    from kavach.reasoning.router import Route, RoutingDecision
    import tempfile
    from pathlib import Path

    class RejectingRouter:
        def route(self, text):
            return RoutingDecision(route=Route.REJECT, confidence=0.1,
                                   reason="not addressed to KAVACH",
                                   intent=None, needs_confirmation=False)

    with tempfile.TemporaryDirectory() as tmp:
        ks = KillSwitch(log=ActionLog(Path(tmp) / "a.jsonl"))
        loop = make_loop(PendingRegistry(), ks, router=RejectingRouter())
        loop.state.reason = "stale from last time"

        loop.respond("mumble")

        assert loop.state.as_dict()["reason"] != "stale from last time"


# ═══ the spoken language reaches the reply ═══
#
# Phase 8 wired "reply in the language you spoke" end to end and it never
# fired once. `transcribe()` called whisper.cpp with no language, which pins
# the decoder to English, and then read the language back out of
# `get_params()` — which returns what we *asked for*, not what Whisper *heard*.
#
# So speaking Hindi produced a mistranslation into broken English ("today my
# meeting is how many hours are"), reported as `en`, answered in English. Every
# other link — the Kokoro Hindi voice, the mapping, the loop passing it to TTS,
# the local model replying in Hindi — was already working and waiting on this.

class FakeWhisper:
    """Stands in for pywhispercpp.Model, whose auto_detect_language returns a
    nested tuple: (("hi", 0.85), {"en": 0.01, "hi": 0.85, ...})."""

    def __init__(self, detected=("hi", 0.85), fail=False):
        self.detected = detected
        self.fail = fail
        self.transcribe_language = None

    def auto_detect_language(self, audio):
        if self.fail:
            raise RuntimeError("detection exploded")
        code, prob = self.detected
        return (code, prob), {code: prob}

    def transcribe(self, audio, **kwargs):
        self.transcribe_language = kwargs.get("language")
        return []


def test_the_detected_language_is_read_not_the_configured_one():
    from kavach.voice.stt import detect_language
    import numpy as np

    model = FakeWhisper(detected=("hi", 0.85))

    assert detect_language(model, np.zeros(16000, dtype="float32")) == "hi"


def test_a_low_confidence_guess_falls_back_to_english():
    """The load-bearing one.

    languages.py already says it: "a reply spoken in the wrong language is
    worse than one spoken in the default". An uncertain detection answering
    you in Mandarin is exactly that failure, so uncertainty means English.
    """
    from kavach.voice.stt import detect_language
    import numpy as np

    model = FakeWhisper(detected=("zh", 0.21))

    assert detect_language(model, np.zeros(16000, dtype="float32")) is None


def test_a_confident_guess_is_kept():
    from kavach.voice.stt import detect_language
    import numpy as np

    assert detect_language(
        FakeWhisper(detected=("hi", 0.90)), np.zeros(16000, dtype="float32")
    ) == "hi"


def test_detection_failing_does_not_break_the_turn():
    """Whisper is mid-sentence with your actual words. A language guess that
    raises must cost you the accent, not the answer."""
    from kavach.voice.stt import detect_language
    import numpy as np

    assert detect_language(
        FakeWhisper(fail=True), np.zeros(16000, dtype="float32")
    ) is None


def test_english_detection_is_reported_as_english():
    from kavach.voice.stt import detect_language
    import numpy as np

    assert detect_language(
        FakeWhisper(detected=("en", 0.99)), np.zeros(16000, dtype="float32")
    ) == "en"


def test_the_decoder_is_never_left_pinned_to_english():
    """The actual fix, and the thing that must never regress.

    Calling transcribe() without a language pins whisper.cpp to English, and
    Hindi comes back as a mistranslation however correctly it is labelled
    afterwards. "auto" is what makes it decode the language that was spoken.
    """
    import numpy as np
    from kavach.voice.stt import SpeechToText

    stt = SpeechToText("large-v3-turbo")
    stt._model = FakeWhisper(detected=("hi", 0.9))

    stt.transcribe(np.zeros(16000, dtype="float32"))

    assert stt._model.transcribe_language == "auto", \
        "the decoder was left on the English default"


def test_the_accurate_pass_decodes_in_the_detected_language(monkeypatch):
    """KAVACH_DETECT_LANGUAGE=full buys the second encoder pass, and then the
    decoder is told exactly what was detected rather than left on auto."""
    import numpy as np
    from kavach.voice.stt import SpeechToText

    monkeypatch.setenv("KAVACH_DETECT_LANGUAGE", "full")
    stt = SpeechToText("large-v3-turbo")
    stt._model = FakeWhisper(detected=("hi", 0.9))

    result = stt.transcribe(np.zeros(16000, dtype="float32"))

    assert stt._model.transcribe_language == "hi"
    assert result.language == "hi"


def test_an_unconfident_turn_decodes_as_auto_not_as_a_guess(monkeypatch):
    import numpy as np
    from kavach.voice.stt import SpeechToText

    monkeypatch.setenv("KAVACH_DETECT_LANGUAGE", "full")
    stt = SpeechToText("large-v3-turbo")
    stt._model = FakeWhisper(detected=("zh", 0.2))

    result = stt.transcribe(np.zeros(16000, dtype="float32"))

    assert result.language is None
    assert stt._model.transcribe_language in (None, "auto", "")


# ═══ deciding the reply language without paying for it twice ═══
#
# whisper.cpp detects the language internally during an `auto` transcribe and
# then does not tell anyone: pywhispercpp's Segment carries only
# probability/t0/t1/text, and get_params() returns the configured value. The
# only way to ask is `auto_detect_language()`, which re-runs the encoder —
# measured at 597 ms against a 609 ms transcribe, so very nearly doubling every
# turn.
#
# The script of the returned text answers the same question for free, and
# answers it exactly for the languages that matter here: Devanagari is Hindi,
# kana is Japanese, Han is Mandarin. It cannot separate Latin-script languages
# from each other, which is why the accurate pass is still available as an
# opt-in rather than deleted.

def test_devanagari_is_recognised_as_hindi():
    from kavach.voice.stt import language_of_script

    assert language_of_script("नमस्ते कवच, आज मेरी मीटिंग कितने बजे है?") == "hi"


def test_plain_english_is_not_mistaken_for_anything():
    from kavach.voice.stt import language_of_script

    assert language_of_script("what time is my meeting today?") is None


def test_japanese_and_mandarin_are_separated():
    from kavach.voice.stt import language_of_script

    assert language_of_script("こんにちは、カヴァチ") == "ja"      # kana wins
    assert language_of_script("你好，今天天气怎么样") == "zh"


def test_a_hinglish_transliteration_stays_english_voiced():
    """Apex and Swift return romanised Hinglish. That is Latin script and is
    read aloud correctly by the English voice — picking the Hindi voice for it
    would phonemise Roman letters with Hindi rules."""
    from kavach.voice.stt import language_of_script

    assert language_of_script("Namaste kavach, aaj meri meeting kitne baje hai?") is None


def test_a_stray_symbol_does_not_decide_the_language():
    """One character in a sentence of another script is noise — a rupee sign
    or a quoted word must not flip the whole reply into another language."""
    from kavach.voice.stt import language_of_script

    assert language_of_script("the price is ₹500 for the meeting room") is None


def test_mostly_hindi_with_an_english_word_is_still_hindi():
    from kavach.voice.stt import language_of_script

    assert language_of_script("मेरी meeting कितने बजे है") == "hi"


def test_empty_text_is_not_a_language():
    from kavach.voice.stt import language_of_script

    assert language_of_script("") is None
    assert language_of_script("   ") is None


# ═══ mirroring the language you actually used ═══
#
# You asked for: English in → English out, Hindi in → Hindi out, Hinglish in →
# Hinglish out. Script alone gets two of the three. Devanagari is unambiguously
# Hindi, but romanised Hinglish and plain English are both Latin, and telling
# them apart is what this adds.
#
# Marker words rather than a language model: "aaj meri meeting kitne baje hai"
# is mostly English letters and entirely Hindi grammar, and a handful of
# extremely common function words identify it without another 600 ms pass.
# Content words are deliberately excluded — "meeting" and "time" appear in both.

def test_romanised_hinglish_is_recognised():
    from kavach.voice.stt import is_romanised_hindi

    assert is_romanised_hindi("aaj meri meeting kitne baje hai")
    assert is_romanised_hindi("kya aap mujhe bata sakte hain")
    assert is_romanised_hindi("mera naam Krishna hai")


def test_plain_english_is_not_hinglish():
    from kavach.voice.stt import is_romanised_hindi

    assert not is_romanised_hindi("what time is my meeting today")
    assert not is_romanised_hindi("open safari and search for flights")
    assert not is_romanised_hindi("remind me about the standup at nine")


def test_a_single_borrowed_word_is_not_hinglish():
    """"namaste" or "chai" in an English sentence is English."""
    from kavach.voice.stt import is_romanised_hindi

    assert not is_romanised_hindi("say namaste to the team for me")
    assert not is_romanised_hindi("order some chai please")


def test_devanagari_is_not_treated_as_romanised():
    from kavach.voice.stt import is_romanised_hindi

    assert not is_romanised_hindi("आज मेरी मीटिंग कितने बजे है")


def test_reply_style_mirrors_what_was_said():
    """The instruction handed to the model, derived from what it heard."""
    from kavach.voice.stt import reply_style

    assert reply_style("आज मेरी मीटिंग कितने बजे है") == "hindi"
    assert reply_style("aaj meri meeting kitne baje hai") == "hinglish"
    assert reply_style("what time is my meeting today") == "english"
    assert reply_style("") == "english"


def test_the_model_is_told_which_style_to_mirror():
    from kavach.reasoning.local import language_instruction

    assert "devanagari" in language_instruction("hindi").lower()
    assert "roman" in language_instruction("hinglish").lower()
    assert language_instruction("english") == ""
