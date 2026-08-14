"""Putting real recordings into the training corpus.

`WakeWordConfig` has no field for real audio — positives can only come from
`target_phrases` via Piper or VoxCPM. That is not an oversight to work around
quietly; it is why v1, v2 and v3 heard nothing but synthesised speech, and why
all three are deaf to this microphone. The config cannot express "train on my
voice", so the injection happens one level below it, at the corpus on disk.

The pipeline this has to slot into, read rather than assumed:

    round 0   reads clip_NNNNNN.wav        (clean TTS originals)
              → augment → RIR → background
              → align_clip_to_end for positives, centre-pad/crop for negatives
              → writes clip_NNNNNN_r0.wav
    round N   reads clip_NNNNNN_r{N-1}.wav → writes clip_NNNNNN_rN.wav
    features  reads ONLY ^clip_\\d{6}_r\\d+\\.wav$

So real takes are injected as **clean round-0 clips**, numbered after the
generated ones, and the library augments, positions, names and extracts them
exactly as it does its own. Nothing here reimplements augmentation — that was
the first design and it would have produced real clips that were augmented
differently from synthetic ones, which is the same class of mistake as
padding them to a different length.

**Oversampling is duplication.** A hundred real takes against ten thousand
synthetic clips is 1%, which will not move a model. Each take is copied N
times under different indices; every copy then draws its own RIR, its own
background noise and its own jitter, so they are siblings rather than
identical rows.
"""

import numpy as np
import pytest

from kavach.voice.wakeinject import (
    CLIP_PATTERN,
    InjectionPlan,
    next_clip_number,
    plan_injection,
)


def make_clips(directory, count: int, suffix: str = "") -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (directory / f"clip_{i:06d}{suffix}.wav").touch()


def make_takes(directory, count: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        (directory / f"take_{i:03d}.wav").touch()


# ═══ where the real clips are numbered from ═══

def test_numbering_continues_after_the_generated_clips(tmp_path):
    """Reusing an index silently overwrites a synthetic clip — the corpus
    shrinks by one and nothing says so."""
    make_clips(tmp_path, 2000)

    assert next_clip_number(tmp_path) == 2000


def test_numbering_ignores_augmented_rounds(tmp_path):
    """`clip_000005_r2.wav` is a derived file, not a source clip. Counting it
    would leave gaps and, worse, suggest more originals than exist."""
    make_clips(tmp_path, 10)
    make_clips(tmp_path, 10, suffix="_r0")
    make_clips(tmp_path, 10, suffix="_r1")

    assert next_clip_number(tmp_path) == 10


def test_an_empty_corpus_starts_at_zero(tmp_path):
    assert next_clip_number(tmp_path) == 0


# ═══ the plan ═══

def test_every_take_is_copied_the_requested_number_of_times(tmp_path):
    takes = tmp_path / "real"
    make_takes(takes, 40)
    (tmp_path / "corpus").mkdir()

    plan = plan_injection(takes, into=tmp_path / "corpus", copies=25)

    assert len(plan.sources) == 40
    assert len(plan.destinations) == 40 * 25


def test_copies_are_numbered_without_collision(tmp_path):
    takes = tmp_path / "real"
    corpus = tmp_path / "corpus"
    make_takes(takes, 5)
    make_clips(corpus, 100)

    plan = plan_injection(takes, into=corpus, copies=3)

    names = [p.name for p in plan.destinations]
    assert len(set(names)) == len(names), "two copies would land on one filename"
    assert all(CLIP_PATTERN.match(n) for n in names), names[:3]
    numbers = sorted(int(CLIP_PATTERN.match(n).group(1)) for n in names)
    assert numbers[0] == 100, "started before the end of the generated clips"
    assert numbers == list(range(100, 115))


def test_copies_are_clean_round_zero_names(tmp_path):
    """Not `_r0`. Injecting an already-augmented name would skip round 0 —
    where positives are aligned to the end of the window — so real clips would
    never be positioned the way synthetic ones are."""
    make_takes(tmp_path / "real", 2)
    (tmp_path / "corpus").mkdir()

    plan = plan_injection(tmp_path / "real", into=tmp_path / "corpus", copies=2)

    assert all("_r" not in p.stem for p in plan.destinations)


def test_the_plan_reports_the_share_of_the_corpus(tmp_path):
    """The number that decides whether this was worth doing. 100 takes against
    10000 synthetic clips is 1% and will not move a model; the point of
    oversampling is to make that share large enough to matter."""
    make_takes(tmp_path / "real", 40)
    make_clips(tmp_path / "corpus", 960)  # also creates the directory

    plan = plan_injection(tmp_path / "real", into=tmp_path / "corpus", copies=1)

    assert plan.share == pytest.approx(0.04, abs=0.005)


def test_no_takes_is_refused_rather_than_silently_doing_nothing(tmp_path):
    (tmp_path / "real").mkdir()

    with pytest.raises(ValueError, match="no recordings"):
        plan_injection(tmp_path / "real", into=tmp_path / "corpus", copies=10)


def test_a_missing_corpus_is_refused(tmp_path):
    """Injecting into a directory the generator has not produced yet would
    write real clips that the next `generate` run wipes."""
    make_takes(tmp_path / "real", 3)

    with pytest.raises(FileNotFoundError):
        plan_injection(tmp_path / "real", into=tmp_path / "nope", copies=2)


# ═══ carrying it out ═══

def test_injection_writes_every_copy(tmp_path):
    from kavach.voice.wakeinject import inject

    takes, corpus = tmp_path / "real", tmp_path / "corpus"
    takes.mkdir()
    corpus.mkdir()
    _write_wav(takes / "take_000.wav", 0.8)
    _write_wav(takes / "take_001.wav", 0.9)

    plan = plan_injection(takes, into=corpus, copies=4)
    written = inject(plan)

    assert len(written) == 8
    assert len(list(corpus.glob("clip_*.wav"))) == 8
    for path in written:
        assert path.stat().st_size > 0


def test_injected_clips_are_readable_by_the_extractor(tmp_path):
    """The clips must survive a round trip as 16kHz mono — the feature
    extractor reads them with soundfile and takes the first channel."""
    import soundfile as sf

    from kavach.voice.wakeinject import inject

    takes, corpus = tmp_path / "real", tmp_path / "corpus"
    takes.mkdir()
    corpus.mkdir()
    _write_wav(takes / "take_000.wav", 0.7)

    written = inject(plan_injection(takes, into=corpus, copies=2))
    audio, rate = sf.read(str(written[0]))

    assert rate == 16_000
    assert audio.ndim == 1
    assert 0.6 < len(audio) / rate < 0.8


def _write_wav(path, seconds: float) -> None:
    import wave

    rng = np.random.default_rng(0)
    samples = (rng.normal(0, 0.1, int(seconds * 16_000)) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16_000)
        handle.writeframes(samples.tobytes())
