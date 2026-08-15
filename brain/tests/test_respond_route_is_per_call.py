"""Which route a turn took must not be read off shared state afterwards.

Observed live 2026-08-16. An API command and a spoken turn overlapped:

    20:36:54  api.command      "search wwe on youtube"
    20:36:54  router.decision  route=claude · "needs tools to act (web search)"
    20:37:03  tool.decision    ToolSearch                      allow
    20:37:04  router.decision  route=local     ← the SPOKEN turn, interleaved
    20:37:06  tool.decision    mcp__kavach-browser__navigate_to allow

The API's response reported `"route":"local"` for a turn that had gone to
the agent and driven a browser. Chrome really was on the YouTube results
page; the label was simply somebody else's.

`respond()` returns a string and records the route as a side effect on
`self.state.route`. The API reads that field *after* `asyncio.to_thread`
returns, so any turn finishing in between overwrites it.

**Cosmetic in the response, not cosmetic in memory.** `remember_turn` skips
a turn when its route is `recall`, and it was being handed this same shared
field — so an interleaved recall turn could suppress the write for an
ordinary command, or a stale label could store a recall turn that should
have been dropped. Both are silent.

`respond(text, out=d)` fills a dict the caller owns. No sharing, so no race.
"""

import pytest

from kavach.api.confirm import PendingRegistry
from tests.test_api import make_loop


@pytest.fixture
def loop(tmp_path):
    from kavach.killswitch.core import KillSwitch
    from kavach.killswitch.log import ActionLog

    registry = PendingRegistry()
    return make_loop(registry, KillSwitch(log=ActionLog(tmp_path / "a.jsonl")))


def test_respond_reports_its_own_route(loop):
    out = {}
    loop.respond("what time is it", out=out)

    assert out.get("route"), "respond() reported no route for its own turn"


def test_two_turns_do_not_share_a_route(loop, monkeypatch):
    """The race, made deterministic: a second turn lands between the first
    finishing and its caller reading the route.

    The routes are **forced** rather than chosen by phrasing. Written first
    with two different-sounding utterances, which both came back `local` —
    this fixture has no agent, so everything falls back and the test could
    not have distinguished a fix from the bug.
    """
    from kavach.reasoning.router import Route, RoutingDecision

    first, second = {}, {}
    loop.respond("what time is it", out=first)
    assert first["route"] == "local"

    monkeypatch.setattr(
        loop.router, "route",
        lambda text, **kw: RoutingDecision(
            Route.REJECT, 1.0, "forced", utterance=text),
    )
    loop.respond("something else entirely", out=second)

    assert second["route"] != first["route"], "the second turn saw the first"
    assert first["route"] == "local", "the second turn overwrote the first"


def test_the_route_survives_a_later_turn(loop):
    """What the API actually does: finish a turn, then read its route after
    other work has happened."""
    mine = {}
    loop.respond("what time is it", out=mine)
    recorded = mine["route"]

    loop.respond("what did I ask you yesterday", out={})   # someone else

    assert mine["route"] == recorded, "another turn rewrote my result"


def test_out_is_optional(loop):
    """Every existing caller passes nothing. Making it required would be a
    silent behaviour change dressed as a signature change."""
    assert isinstance(loop.respond("what time is it"), str)


def test_the_api_does_not_read_shared_state_for_memory():
    """Asserted on the code: the fix is that the field is never consulted,
    not that it currently happens to be right."""
    import ast
    import inspect

    from kavach.api import app

    for call in [n for n in ast.walk(ast.parse(inspect.getsource(app)))
                 if isinstance(n, ast.Call)
                 and getattr(n.func, "id", None) == "remember_turn"]:
        for keyword in call.keywords:
            if keyword.arg != "route":
                continue
            source = ast.unparse(keyword.value)
            assert "state" not in source, (
                f"route read from shared loop state ({source}) — an "
                f"interleaved turn overwrites it, and remember_turn skips "
                f"on 'recall'"
            )
