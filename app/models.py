"""Pydantic v2 请求/响应模型。

时值、stretch 倍率与 mirror 轴都用正整数（或任意整数）的 ``{n, d}`` 对表示，
服务端再用 :mod:`fractions` 精确约分；不接受也不产生任何浮点音高/拍点。
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ---------- 规模常量（亦见 README「复杂度与限制」） ----------
MAX_NODES = 2000
MAX_REPEAT_COUNT = 1_000_000_000
MAX_BASE_ITEMS = 50_000  # 单个基础乐句最多原始项数
MAX_EVENTS_EXPANDED = 1_000_000_000_000_000  # 规范事件数硬上限 10^15
MAX_COMPARE_STEPS = 4_000_000  # 压缩比对允许的结构步数
MAX_PITCH = 127


# ---------- 请求 ----------
class FractionIn(BaseModel):
    """有理数 n/d；默认 d=1，d 必须为正整数。是否允许负值由使用方再校验。"""

    model_config = ConfigDict(extra="forbid")

    n: int
    d: int = 1

    @field_validator("d")
    @classmethod
    def _denominator_positive(cls, v: int) -> int:
        if v <= 0:
            raise ValueError("分母必须为正整数")
        return v


class _ItemBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoteItem(_ItemBase):
    """发声项：MIDI 0..127 音高 + 正时值 + 可选 tie_next。"""

    type: Literal["note"]
    pitch: int = Field(ge=0, le=127)
    duration: FractionIn
    tie_next: bool = False


class RestItem(_ItemBase):
    """休止项：正时值。"""

    type: Literal["rest"]
    duration: FractionIn


Item = Annotated[NoteItem | RestItem, Field(discriminator="type")]


class _NodeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=0)


class PhraseNode(_NodeBase):
    op: Literal["phrase"]
    items: list[Item]


class ConcatNode(_NodeBase):
    op: Literal["concat"]
    children: list[int]


class RepeatNode(_NodeBase):
    op: Literal["repeat"]
    child: int
    count: int = Field(ge=0, le=MAX_REPEAT_COUNT)


class TransposeNode(_NodeBase):
    op: Literal["transpose"]
    child: int
    k: int


class PitchMirrorNode(_NodeBase):
    op: Literal["pitch_mirror"]
    child: int
    axis: FractionIn


class ReverseNode(_NodeBase):
    op: Literal["reverse"]
    child: int


class StretchNode(_NodeBase):
    op: Literal["stretch"]
    child: int
    q: FractionIn


Node = Annotated[
    PhraseNode | ConcatNode | RepeatNode | TransposeNode | PitchMirrorNode | ReverseNode | StretchNode,
    Field(discriminator="op"),
]


class AnalyzeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_root: int = Field(ge=0)
    handwritten_root: int = Field(ge=0)
    nodes: list[Node]


# ---------- 响应 ----------
class FractionOut(BaseModel):
    n: int
    d: int


class EventOut(BaseModel):
    kind: Literal["note", "rest"]
    pitch: int | None = None
    duration: FractionOut


class SourceStep(BaseModel):
    node_id: int
    repeat_iteration: int | None = None
    item_index: int | None = None


class SideView(BaseModel):
    status: Literal["event", "ended"]
    event: EventOut | None = None
    source_chain: list[SourceStep] = Field(default_factory=list)


class FirstDifference(BaseModel):
    beat: FractionOut
    expected: SideView
    handwritten: SideView


class AnalyzeResponse(BaseModel):
    equal: bool
    event_count: int
    total_duration: FractionOut
    first_difference: FirstDifference | None = None
