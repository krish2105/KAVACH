"""Round-trip latency instrumentation.

Spec §9 puts this ahead of quality for Phase 2: "get latency right first".
Every stage stamps itself, so when the loop feels slow we know *which* stage
is slow instead of guessing.

Numbers are reported honestly — no smoothing, no dropping the first run. The
first turn is always the slowest (model warm-up) and pretending otherwise
would hide the thing a user actually notices.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Span:
    name: str
    started: float
    ended: float | None = None

    @property
    def ms(self) -> float:
        end = self.ended if self.ended is not None else time.perf_counter()
        return (end - self.started) * 1000.0


@dataclass
class TurnTimer:
    """Times one wake→speak turn, stage by stage."""

    spans: list[Span] = field(default_factory=list)
    _open: dict[str, Span] = field(default_factory=dict)
    started: float = field(default_factory=time.perf_counter)

    def start(self, name: str) -> None:
        span = Span(name, time.perf_counter())
        self._open[name] = span
        self.spans.append(span)

    def stop(self, name: str) -> float:
        span = self._open.pop(name, None)
        if span is None:
            return 0.0
        span.ended = time.perf_counter()
        return span.ms

    def total_ms(self) -> float:
        return (time.perf_counter() - self.started) * 1000.0

    def as_dict(self) -> dict[str, float]:
        out = {span.name: round(span.ms, 1) for span in self.spans}
        out["total"] = round(self.total_ms(), 1)
        return out

    def render(self) -> str:
        """One line per stage, plus the number that actually matters: the gap
        between the user finishing speaking and hearing the first audio back."""
        parts = [f"{span.name}={span.ms:.0f}ms" for span in self.spans]
        return "  ".join(parts) + f"  TOTAL={self.total_ms():.0f}ms"
