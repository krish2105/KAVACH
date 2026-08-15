"""Phase 33 — the proposal queue. Nothing in it runs on its own.

PROPOSE-tier actions queue instead of interrupting. The user approves, rejects
or edits them in a batch.

**There is no auto-execute timeout, and that is the load-bearing property.**
An unreviewed item sits, or expires unexecuted. It never runs by default. The
§7 confirmation already works this way — a timeout is a denial — and a queue
that executed on expiry would be the opposite rule living beside it, which is
how one of two rules quietly becomes the one that matters.

Phase 6's API exists (`reach-6`: FastAPI on 127.0.0.1:8770, bearer token,
pending-confirmation flow), so this has a surface to be reviewed through.
Confirmed before building, as the spec asked.
"""

import pytest

from kavach.autonomy.proposals import Proposal, ProposalQueue, Status


@pytest.fixture
def queue(tmp_path):
    return ProposalQueue(path=tmp_path / "proposals.json")


# ═══ nothing executes itself ═══

def test_a_new_proposal_is_pending_not_approved(queue):
    item = queue.add("file.delete", "delete ~/Downloads/old.zip")

    assert item.status is Status.PENDING


def test_an_expired_proposal_is_not_executed(queue):
    """The property this phase exists for. An item nobody reviewed must
    never run — the §7 confirmation treats a timeout as a denial, and a queue
    that executed on expiry would be the opposite rule living next door."""
    item = queue.add("file.delete", "delete something", ttl_seconds=0)

    queue.sweep(now=item.created_at + 1)

    assert queue.get(item.id).status is Status.EXPIRED


def test_expired_is_a_distinct_state_from_rejected(queue):
    """"You said no" and "nobody looked" are different facts, and Phase 34
    learns from approval history — counting an expiry as a rejection would
    teach it something that never happened."""
    assert Status.EXPIRED is not Status.REJECTED


def test_nothing_is_executable_until_approved(queue):
    item = queue.add("file.delete", "delete something")

    assert not queue.ready_to_run()

    queue.approve(item.id)
    assert [p.id for p in queue.ready_to_run()] == [item.id]


def test_a_rejected_proposal_never_becomes_ready(queue):
    item = queue.add("file.delete", "delete something")
    queue.reject(item.id)

    assert not queue.ready_to_run()


# ═══ review ═══

def test_approving_an_unknown_id_is_refused(queue):
    """Silently succeeding on an id that does not exist would let a caller
    believe it approved something."""
    with pytest.raises(KeyError):
        queue.approve("no-such-id")


def test_an_expired_item_cannot_be_approved_later(queue):
    """Approving something that already lapsed would resurrect an action the
    user never actually reviewed in time."""
    item = queue.add("file.delete", "x", ttl_seconds=0)
    queue.sweep(now=item.created_at + 1)

    with pytest.raises(ValueError):
        queue.approve(item.id)


def test_editing_a_proposal_keeps_it_pending(queue):
    """An edited proposal is a different proposal and needs approving on its
    own terms."""
    item = queue.add("file.delete", "delete ~/Downloads/old.zip")
    queue.edit(item.id, "delete ~/Downloads/old.zip and ~/Downloads/new.zip")

    updated = queue.get(item.id)
    assert updated.status is Status.PENDING
    assert "new.zip" in updated.description


def test_batch_approval_works(queue):
    ids = [queue.add("file.delete", f"delete {n}").id for n in "abc"]

    queue.approve_many(ids)

    assert len(queue.ready_to_run()) == 3


# ═══ the ceiling still applies ═══

def test_a_ceilinged_action_can_be_proposed(queue):
    """PROPOSE is exactly the tier a destructive action is allowed to reach."""
    item = queue.add("mail.send", "send the draft to Vatsal")

    assert item.status is Status.PENDING


def test_approving_does_not_bypass_the_tool_gate(queue):
    """Approving a proposal authorises it to be ATTEMPTED. The gate still
    runs when it executes — the kill switch, the confirmation, the log. A
    queue approval is not a second permission system."""
    import inspect

    from kavach.autonomy import proposals

    from ._sourcecheck import code_text

    # `code_text`, not raw source: the module's docstring names these very
    # words to explain that they are forbidden, and grep cannot tell an
    # explanation from an implementation. Third time this has come up today.
    source = code_text(inspect.getmodule(proposals))
    for forbidden in ("subprocess", "osascript", "Popen", "os.system"):
        assert forbidden not in source, (
            f"{forbidden} in proposals.py — the queue records decisions, it "
            f"does not execute them"
        )


# ═══ §7 ═══

class Log:
    def __init__(self):
        self.entries = []

    def append(self, event, **fields):
        self.entries.append((event, fields))


def test_every_decision_is_logged(tmp_path):
    log = Log()
    queue = ProposalQueue(path=tmp_path / "p.json", log_=log)
    item = queue.add("file.delete", "delete x")
    queue.approve(item.id)

    events = [e for e, _ in log.entries]
    assert "proposal.added" in events
    assert "proposal.approved" in events


def test_the_queue_survives_a_reload(tmp_path):
    path = tmp_path / "p.json"
    item = ProposalQueue(path=path).add("file.delete", "delete x")

    assert ProposalQueue(path=path).get(item.id).description == "delete x"
