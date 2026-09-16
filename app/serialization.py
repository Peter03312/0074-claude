"""内部 CursorState / Term 到 Pydantic 响应结构的转换（全部精确分数）。"""

from __future__ import annotations

from fractions import Fraction

from app.models import (
    EventOut,
    FirstDifference,
    FractionOut,
    SideView,
    SourceStep,
)

from .core.compare import Difference
from .core.cursor import CursorState
from .core.terms import Term


def fraction_out(value: Fraction) -> FractionOut:
    return FractionOut(n=value.numerator, d=value.denominator)


def _event_out(state: CursorState) -> EventOut:
    ev = state.event
    if ev.pitch is None:
        return EventOut(kind="rest", pitch=None, duration=fraction_out(ev.duration))
    return EventOut(
        kind="note", pitch=ev.pitch, duration=fraction_out(ev.duration)
    )


def _chain(state: CursorState) -> list[SourceStep]:
    return [
        SourceStep(
            node_id=s.node_id,
            repeat_iteration=s.iteration,
            item_index=s.item_index,
        )
        for s in state.chain
    ]


def state_to_side(state: CursorState | None) -> SideView:
    if state is None:
        return SideView(status="ended", event=None, source_chain=[])
    return SideView(status="event", event=_event_out(state), source_chain=_chain(state))


def difference_to_payload(diff: Difference) -> FirstDifference:
    return FirstDifference(
        beat=fraction_out(diff.beat),
        expected=state_to_side(diff.expected),
        handwritten=state_to_side(diff.handwritten),
    )


def term_summary(term: Term) -> dict:
    return {
        "count": term.count,
        "duration": term.duration,
    }
