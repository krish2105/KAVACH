"""The router (spec §5).

> Don't just always call Claude — that's slow and burns credit for "what time
> is it". A simple heuristic first pass works fine.

Two passes, cheapest first:

1. **Heuristics.** Deterministic, sub-millisecond, and testable without a
   model running. Catches the obvious ends of the spectrum: "what time is it"
   is never a Claude question, and "read my emails and draft a reply" is never
   a 4B-model question.
2. **The local model.** Only for what the heuristics can't settle — one
   forward pass to classify, per §5.

Anything still ambiguous **escalates**. A 4B model answering confidently about
something it cannot do is worse than the extra second, and the confidence it
reports is what the orb's outer shell renders (§4 #3) — so a wrong-but-certain
route also lies to the user visually.

Destructive intent is flagged independently of the route: *how* a request is
reasoned about has nothing to do with whether it needs confirmation (§7).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from enum import Enum

log = logging.getLogger("kavach.reasoning.router")


class Route(str, Enum):
    LOCAL = "local"
    CLAUDE = "claude"
    #: Not a command at all — silence, noise, or a Whisper confabulation.
    REJECT = "reject"


@dataclass
class RoutingDecision:
    route: Route
    confidence: float
    reason: str
    needs_confirmation: bool = False
    utterance: str = ""
    #: Simple-intent label ("clock", "app control", …) when the
    #: heuristics matched one. Lets the loop try a deterministic handler
    #: before spending a model call.
    intent: str = ""

    def as_dict(self) -> dict:
        return {
            "route": self.route.value,
            "confidence": round(self.confidence, 3),
            "reason": self.reason,
            "needs_confirmation": self.needs_confirmation,
            "intent": self.intent,
            "utterance": self.utterance,
        }


# ——— §7: destructive or externally visible ———
# Matched as whole words: "sends" must hit, "sender" must not, and "buy" must
# not fire on "buying guide".
_DESTRUCTIVE = [
    r"\bdelete[sd]?\b", r"\bremove[sd]?\b", r"\btrash\b", r"\bempty the trash\b",
    r"\bsend[s]?\b", r"\breply\b", r"\bforward[s]?\b",
    r"\bpost[s]?\b", r"\bpublish\b", r"\bshare[s]?\b", r"\btweet\b",
    r"\bbuy[s]?\b", r"\bpurchase[sd]?\b", r"\border[s]?\b", r"\bpay\b",
    r"\bsubmit[s]?\b", r"\bconfirm\b",
    r"\binstall[s]?\b", r"\buninstall\b",
    r"\bshut ?down\b", r"\brestart\b", r"\blog ?out\b",
    r"\bmove to trash\b", r"\bwipe\b", r"\bformat\b", r"\boverwrite\b",
]
_DESTRUCTIVE_RE = re.compile("|".join(_DESTRUCTIVE), re.I)


# Questions about a *completed* action are read-only: "what did she send me"
# asks about the past, it does not send anything. Narrow on purpose — only
# past-tense and perfect interrogatives. "can you delete the draft" is
# phrased as a question but is still an instruction, so it stays flagged.
_PAST_QUERY_RE = re.compile(
    r"^\s*(what|who|whom|when|where|which|how many|how much)\b[^?]*\b"
    r"(did|has|have|had|was|were)\b",
    re.I,
)
_PAST_TENSE_ONLY_RE = re.compile(
    r"^\s*(what|who|whom|when|where|which)\b[^?]*\b"
    r"(sent|deleted|removed|posted|shared|bought|ordered|paid|submitted)\b",
    re.I,
)


def looks_destructive(utterance: str) -> bool:
    """True if the request would send, delete, buy, submit or change a system
    setting — the §7 list.

    Over-inclusive **for imperatives**, on purpose: a false positive costs one
    confirmation, a false negative costs an email you didn't mean to send.
    Questions about what already happened are excluded, because demanding
    confirmation to *answer* one makes the assistant exhausting to use.
    """
    text = utterance or ""
    if not _DESTRUCTIVE_RE.search(text):
        return False
    if _PAST_QUERY_RE.search(text) or _PAST_TENSE_ONLY_RE.search(text):
        return False
    return True


# ——— simple intents: no reasoning required ———
_SIMPLE_PATTERNS = [
    (r"\bwhat('?s| is) the (time|date)\b", "clock"),
    (r"\bwhat time is it\b", "clock"),
    (r"\bwhat('?s| is) today('?s)? date\b", "clock"),
    (r"\b(open|launch|start|quit|close)\s+\w+", "app control"),
    (r"\b(volume|sound)\s+(up|down)\b", "system control"),
    (r"\b(mute|unmute)\b", "system control"),
    (r"\bbattery( level| percentage)?\b", "system query"),
    (r"\b(play|pause|next track|previous track|skip)\b", "media control"),
    (r"\bwhat('?s| is) the (weather|battery)\b", "simple query"),
]
_SIMPLE_RE = [(re.compile(p, re.I), label) for p, label in _SIMPLE_PATTERNS]

# ——— signals that a request needs judgement ———
_COMPLEX_PATTERNS = [
    (r"\band then\b", "multi-step"),
    (r"\bafter that\b", "multi-step"),
    (r"\bsummar(ise|ize)\b", "comprehension"),
    # Verb only. "draft a reply" needs judgement; "delete the draft" is a
    # noun and has nothing to do with generation.
    (r"\bdraft\s+(a|an|the|me|up)\b", "generation"),
    (r"\bwrite\s+(a|an|me)\b", "generation"),
    (r"\bcompare\b", "comparison"),
    (r"\bfigure out\b", "open-ended reasoning"),
    (r"\bwork out\b", "open-ended reasoning"),
    (r"\bwhy\b.*\b(fail|failing|broken|wrong)\b", "diagnosis"),
    (r"\bwhich .*\b(is|are)\b.*\bwrong\b", "judgement"),
    (r"\btell me (what|which|why|how)\b", "judgement"),
    (r"\blook at\b", "inspection"),
    (r"\bread\b.*\band\b", "read-then-act"),
    # Screen questions need vision + judgement, so they escalate. Peekaboo
    # was installed and gated in Phase 0 but never reachable, because the
    # router had no intent that led here.
    (r"\bwhat('?s| is| am i)\b.*\b(on|in)\b.*\b(screen|display|monitor)\b", "screen query"),
    (r"\b(read|describe|look at|check)\b.*\bscreen\b", "screen query"),
    (r"\bwhat does (this|that|it) say\b", "screen query"),
    (r"\bwhat am i looking at\b", "screen query"),
    (r"\bon my (screen|display)\b", "screen query"),
    (r"\bfix\b", "open-ended"),
    (r"\breview\b", "judgement"),
    # Recall needs the memory store plus judgement about what is relevant.
    (r"\bwhat did (i|we|you) (say|decide|discuss)\b", "recall"),
    (r"\bremind me (what|about|of)\b", "recall"),
    (r"\bdo you remember\b", "recall"),
    (r"\bin my (notes|documents|files)\b", "recall"),
    (r"\bsearch my (notes|documents|files)\b", "recall"),
]
_COMPLEX_RE = [(re.compile(p, re.I), label) for p, label in _COMPLEX_PATTERNS]

# Whisper confabulations — see kavach/voice/stt.py. Repeated here because the
# router must be safe to call from anywhere, not only behind the voice loop.
_JUNK = {
    "", "thank you.", "thank you", "thanks for watching!", "you", "you.",
    "bye.", "bye", ".", "so", "so.", "oh", "oh.",
}

#: Below this many words, a request is almost certainly a simple command.
_SHORT_UTTERANCE_WORDS = 4


class Router:
    def __init__(self, local_client=None, escalate_threshold: float = 0.6):
        self.local = local_client
        self.escalate_threshold = escalate_threshold
        #: Every decision, for tuning the split (§5 asks for this explicitly).
        self.decisions: list[RoutingDecision] = field(default_factory=list)
        self.decisions = []

    def route(self, utterance: str) -> RoutingDecision:
        decision = self._decide(utterance)
        self.decisions.append(decision)
        log.info(
            "route=%s conf=%.2f confirm=%s — %s",
            decision.route.value, decision.confidence,
            decision.needs_confirmation, decision.reason,
        )
        return decision

    def _decide(self, utterance: str) -> RoutingDecision:
        text = (utterance or "").strip()

        if text.casefold() in _JUNK:
            return RoutingDecision(
                Route.REJECT, 0.0, "empty or known STT confabulation",
                utterance=text,
            )

        confirm = looks_destructive(text)

        # Complex signals win over simple ones: "open Safari and then search
        # for flights" starts like an app-control command but isn't one.
        for pattern, label in _COMPLEX_RE:
            if pattern.search(text):
                return RoutingDecision(
                    Route.CLAUDE, 0.55, f"needs judgement ({label})",
                    needs_confirmation=confirm, utterance=text,
                )

        for pattern, label in _SIMPLE_RE:
            if pattern.search(text):
                return RoutingDecision(
                    Route.LOCAL, 0.92, f"simple intent ({label})",
                    needs_confirmation=confirm, utterance=text, intent=label,
                )

        # Heuristics didn't settle it. §5's second pass: one cheap forward
        # pass through the local model to classify.
        if self.local is not None:
            classified = self._classify_with_local(text)
            if classified is not None:
                classified.needs_confirmation = confirm
                return classified

        # Still unsettled. Short utterances are almost always simple commands;
        # anything longer escalates rather than being guessed at.
        words = len(text.split())
        if words <= _SHORT_UTTERANCE_WORDS and not confirm:
            return RoutingDecision(
                Route.LOCAL, 0.62, f"short utterance ({words} words), likely simple",
                needs_confirmation=confirm, utterance=text,
            )

        return RoutingDecision(
            Route.CLAUDE, 0.4,
            "ambiguous — escalating rather than guessing",
            needs_confirmation=confirm, utterance=text,
        )

    def _classify_with_local(self, text: str) -> RoutingDecision | None:
        """Ask the local model to classify. Returns None if it is unavailable
        or unclear — the caller then falls through to the safe default."""
        try:
            verdict = self.local.classify_intent(text)
        except Exception:
            log.warning("local classifier failed; falling through", exc_info=True)
            return None

        if not verdict:
            return None

        label = str(verdict.get("intent", "")).lower()

        # The model's own `confidence` is deliberately ignored: qwen3:4b
        # returns exactly 0.95 on every call, right or wrong. It is a constant,
        # not a measurement, and §4 #3 renders this number as the orb's shell
        # opacity — so passing it through would show the user a confidence
        # that never moves. Confidence comes from *how* we decided instead.
        if label == "simple":
            return RoutingDecision(
                Route.LOCAL, 0.75,
                "local model classified as simple (heuristics were unsure)",
                utterance=text,
            )
        if label == "complex":
            return RoutingDecision(
                Route.CLAUDE, 0.5,
                "local model classified as complex", utterance=text,
            )
        return None
