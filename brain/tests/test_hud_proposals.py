"""Phase 33's HUD surface — the queue where you can actually see it.

The spec asked for "a batch review surface in the HUD". The API exists and the
CLI exists; the orb, which is the thing actually on screen, showed nothing. A
queue you have to remember to go and check is a queue that fills up.

**The snapshot contract is enforced by a test** (`test_voice.py` reads
`kavachState.ts` and asserts field-identity), so adding this field means
changing both sides together. That test is why this cannot drift.

The HUD gets a **count and the pending items**, not a live feed of everything —
the orb is a glance surface, and a wall of proposals is as unreadable as none.
"""

import pytest

from kavach.autonomy.proposals import ProposalQueue
from kavach.voice.loop import VoiceState


def test_the_snapshot_carries_proposals():
    """A field the HUD can render. Absent, the orb cannot know the queue
    exists."""
    assert "proposals" in VoiceState().as_dict()


def test_an_empty_queue_is_an_empty_list_not_null(tmp_path):
    """`null` and `[]` render differently in TypeScript and one of them is a
    crash. The contract test catches the name; this catches the shape."""
    snapshot = VoiceState().as_dict()

    assert snapshot["proposals"] == []


def test_proposals_carry_what_the_user_needs_to_decide(tmp_path):
    """An id to act on and a description to judge. Anything less is a badge
    with no way to review what it counts."""
    queue = ProposalQueue(path=tmp_path / "p.json")
    item = queue.add("write_file", "write /tmp/report.txt")

    state = VoiceState()
    state.proposals = [p.as_dict() for p in queue.pending()]
    rendered = state.as_dict()["proposals"]

    assert rendered[0]["id"] == item.id
    assert "/tmp/report.txt" in rendered[0]["description"]


def test_the_typescript_contract_still_matches():
    """CLAUDE.md: VoiceState.as_dict() must stay field-identical to
    KavachSnapshot. A rename here silently breaks the HUD, and silence is the
    failure mode this whole project keeps finding."""
    from pathlib import Path

    source = (Path(__file__).resolve().parents[2] / "apps" / "orb" / "lib"
              / "kavachState.ts").read_text()
    start = source.index("interface KavachSnapshot")
    block = source[start:source.index("}", start)]

    assert "proposals" in block, (
        "kavachState.ts has no `proposals` field — the Python side would be "
        "sending something the HUD cannot type"
    )
