"""Put real recordings into the wake-word training corpus.

`WakeWordConfig` has no field for real audio: positives can only come from
``target_phrases`` through Piper or VoxCPM. That is not a gap to route around
quietly — it is the reason v1, v2 and v3 heard nothing but synthesised speech,
and the reason all three are deaf to this microphone (0.858 as a file, 0.019
through the mic). The config cannot express "train on my voice", so the
injection happens one level below it, at the corpus on disk.

The pipeline this slots into, read from ``livekit/wakeword/data/augment.py``
rather than assumed::

    round 0   reads clip_NNNNNN.wav            (clean originals)
              → augment_clip → apply_rir → mix_with_background
              → align_clip_to_end for positives, centre-pad/crop for negatives
              → writes clip_NNNNNN_r0.wav
    round N   reads clip_NNNNNN_r{N-1}.wav     → writes clip_NNNNNN_rN.wav
    features  reads ONLY ^clip_\\d{6}_r\\d+\\.wav$

So real takes go in as **clean round-0 clips**, numbered after the generated
ones, and the library augments, positions, names and extracts them exactly as
it does its own. Nothing here reimplements augmentation. The first design did,
and it would have produced real clips augmented differently from synthetic
ones — the same class of mistake as padding them to a different length, and
just as invisible afterwards.

**Oversampling is duplication.** A hundred real takes against ten thousand
synthetic clips is one percent, which will not move a model. Each take is
copied N times under different indices, and every copy then draws its own room
impulse response, its own background noise and its own alignment jitter — so
they are siblings, not duplicated rows.
"""

from __future__ import annotations

import logging
import re
import shutil
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("kavach.voice.wakeinject")

#: A clean round-0 source clip. The `_rN` files are derived, and the feature
#: extractor reads only those — so what goes in here must NOT carry a suffix,
#: or it skips round 0 and never gets aligned the way positives are.
CLIP_PATTERN = re.compile(r"^clip_(\d{6})\.wav$")

_TAKE_PATTERN = re.compile(r"^take_\d{3,}\.wav$")


@dataclass
class InjectionPlan:
    """What would be written, before anything is."""

    sources: list[Path]
    destinations: list[Path]
    #: Real clips as a fraction of the corpus afterwards. The number that says
    #: whether this was worth doing at all.
    share: float
    existing: int

    def describe(self) -> str:
        return (
            f"{len(self.sources)} recordings × "
            f"{len(self.destinations) // max(1, len(self.sources))} copies = "
            f"{len(self.destinations)} clips, joining {self.existing} generated "
            f"→ real audio is {self.share * 100:.0f}% of the corpus"
        )


def next_clip_number(corpus: Path) -> int:
    """The first free clip index in a corpus directory.

    Counts only clean originals. An augmented `clip_000005_r2.wav` is a derived
    file; counting it would leave gaps and imply more originals than exist.
    """
    corpus = Path(corpus)
    if not corpus.exists():
        return 0

    highest = -1
    for path in corpus.glob("clip_*.wav"):
        match = CLIP_PATTERN.match(path.name)
        if match:
            highest = max(highest, int(match.group(1)))
    return highest + 1


def plan_injection(takes_dir: Path, into: Path, copies: int) -> InjectionPlan:
    """Work out every copy that would be made, and refuse rather than no-op.

    Nothing is written here. The plan is separate so the share of the corpus
    can be seen *before* committing to it — 1% is not worth a training run, and
    finding that out afterwards costs hours.
    """
    takes_dir, into = Path(takes_dir), Path(into)

    sources = sorted(p for p in takes_dir.glob("*.wav")
                     if _TAKE_PATTERN.match(p.name))
    if not sources:
        raise ValueError(
            f"no recordings in {takes_dir} — run `uv run kavach-wakerecord` first"
        )
    if not into.exists():
        # Injecting into a corpus that does not exist yet would write clips the
        # next `generate` run wipes, and the training would look normal.
        raise FileNotFoundError(
            f"{into} does not exist — generate the synthetic corpus first"
        )
    if copies < 1:
        raise ValueError("copies must be at least 1")

    existing = next_clip_number(into)
    destinations = [
        into / f"clip_{existing + i:06d}.wav"
        for i in range(len(sources) * copies)
    ]
    total = existing + len(destinations)

    return InjectionPlan(
        sources=sources,
        destinations=destinations,
        share=len(destinations) / total if total else 0.0,
        existing=existing,
    )


def inject(plan: InjectionPlan) -> list[Path]:
    """Carry out a plan. Returns the paths written.

    A straight copy: every clip is already 16kHz mono from the recorder, and
    re-encoding here would be a second place for the format to drift from what
    the extractor reads.
    """
    written: list[Path] = []
    for i, destination in enumerate(plan.destinations):
        source = plan.sources[i % len(plan.sources)]
        shutil.copyfile(source, destination)
        written.append(destination)

    log.info("injected %d clips from %d recordings into %s",
             len(written), len(plan.sources), plan.destinations[0].parent)
    return written
