"""DAG 的压缩表示：不可变 Term。

一个 Term 描述某个 DAG 节点求值后的**规范事件序列**，但绝不显式展开
repeat。种类：

* ``PHRASE``  基础乐句（连音合并、连续休止合并后的显式事件）；
* ``CONCAT``  顺接：预编译为 body / token 槽序列，跨拼接边界的连续休止
  合并为一个 token；
* ``REPEAT``  整数次重复：利用首/尾休止信息在边界合并；
* ``REVERSE`` / ``STRETCH`` / ``PITCH`` 单孩子包装项；
* ``EMPTY``   空序列（空乐句或 count=0 的 repeat）。

所有时值都是 Fraction，所有计数都是 Python 大整数。
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction

from .events import ChainStep, Event

PHRASE = "phrase"
CONCAT = "concat"
REPEAT = "repeat"
REVERSE = "reverse"
STRETCH = "stretch"
PITCH = "pitch"
EMPTY = "empty"

BODY = "body"
TOKEN = "token"


@dataclass(frozen=True, slots=True)
class Slot:
    """concat 编译槽。

    body ：某孩子 [lo, hi) 的显式事件；
    token：跨若干孩子合并出的一个休止（duration 已定）。
    """

    kind: str
    length: int
    # body
    term: Term | None = None
    lo: int = 0
    hi: int = -1
    # token
    duration: Fraction | None = None
    path: tuple[ChainStep, ...] = ()


def step(node_id: int, iteration: int | None = None, item: int | None = None) -> ChainStep:
    return ChainStep(node_id=node_id, iteration=iteration, item_index=item)


class Term:
    __slots__ = (
        "kind",
        "node_id",
        "count",
        "duration",
        "pmin",
        "pmax",
        "first_rest",
        "last_rest",
        "first_dur",
        "last_dur",
        "first_rel",
        "last_rel",
        # phrase
        "events",
        "eidx",
        "eprefix",
        # 单孩子
        "child",
        # repeat
        "rep_c",
        "rep_m",
        # stretch
        "q",
        # pitch 仿射 p -> s*p + t（s ∈ {1,-1}，t 为整数）
        "s",
        "t",
        # concat
        "slots",
        "slot_beat",
        "slot_start",
    )

    def __init__(self, kind: str, node_id: int):
        self.kind = kind
        self.node_id = node_id
        self.count: int = 0
        self.duration: Fraction = Fraction(0)
        self.pmin: int | None = None
        self.pmax: int | None = None
        self.first_rest = False
        self.last_rest = False
        self.first_dur: Fraction | None = None
        self.last_dur: Fraction | None = None
        self.first_rel: tuple[ChainStep, ...] = ()
        self.last_rel: tuple[ChainStep, ...] = ()
        self.events: tuple[Event, ...] = ()
        self.eidx: tuple[int, ...] = ()
        self.eprefix: tuple[Fraction, ...] = ()
        self.child: Term | None = None
        self.rep_c: int = 0
        self.rep_m: int = 0
        self.q: Fraction = Fraction(1)
        self.s: int = 1
        self.t: int = 0
        self.slots: tuple[Slot, ...] = ()
        self.slot_beat: tuple[Fraction, ...] = ()
        self.slot_start: tuple[int, ...] = ()

    @property
    def empty(self) -> bool:
        return self.count == 0

    # ---------- 相对来源链 ----------
    def rel_path(self, p: int) -> tuple[ChainStep, ...]:
        """项内第 p 个事件（零基）的相对来源链，含本项自身节点步。"""
        k = self.kind
        if k == PHRASE:
            return (step(self.node_id, item=self.eidx[p]),)
        if k == REVERSE:
            return (step(self.node_id),) + self.child.rel_path(self.count - 1 - p)
        if k in (STRETCH, PITCH):
            return (step(self.node_id),) + self.child.rel_path(p)
        if k == REPEAT:
            return self._repeat_rel_path(p)
        if k == CONCAT:
            slot, k_in = self._slot_at(p)
            if slot.kind == TOKEN:
                return (step(self.node_id),) + slot.path
            return (step(self.node_id),) + slot.term.rel_path(slot.lo + k_in)
        raise AssertionError("empty term has no path")

    def _repeat_merges(self) -> bool:
        return self.child.first_rest and self.child.last_rest

    def _repeat_rel_path(self, p: int) -> tuple[ChainStep, ...]:
        c, m, child = self.rep_c, self.rep_m, self.child
        if not self._repeat_merges():
            i, t = divmod(p, m)
            return (ChainStep(self.node_id, i),) + child.rel_path(t)
        # 合并模式分组：p==0 首休止；之后每组 m-1 个：interior + 休止
        if p == 0:
            return (ChainStep(self.node_id, 0),) + child.rel_path(0)
        g, r = divmod(p - 1, m - 1)
        if r < m - 2:
            return (ChainStep(self.node_id, g),) + child.rel_path(r + 1)
        # 休止：g<c-1 为边界（取较早副本 g 的尾部），g==c-1 为最终尾部
        it = min(c - 1, g)
        return (ChainStep(self.node_id, it),) + child.last_rel

    def _slot_at(self, p: int) -> tuple[Slot, int]:
        idx = bisect_right(self.slot_start, p) - 1
        return self.slots[idx], p - self.slot_start[idx]

    # ---------- 拍点 ----------
    def prefix_duration(self, p: int) -> Fraction:
        """本项前 p 个事件的总时值（第 p 个事件的零基起点拍点）。"""
        if p <= 0:
            return Fraction(0)
        if p >= self.count:
            return self.duration
        k = self.kind
        if k == PHRASE:
            return self.eprefix[p]
        if k == STRETCH:
            return self.q * self.child.prefix_duration(p)
        if k == PITCH:
            return self.child.prefix_duration(p)
        if k == REVERSE:
            n = self.count
            return self.child.duration - self.child.prefix_duration(n - p)
        if k == REPEAT:
            return self._repeat_prefix(p)
        if k == CONCAT:
            return self._concat_prefix(p)
        return Fraction(0)

    def _repeat_prefix(self, p: int) -> Fraction:
        m, child = self.rep_m, self.child
        d = child.duration
        if not self._repeat_merges():
            i, t = divmod(p, m)
            return i * d + child.prefix_duration(t)
        # 合并模式
        if p == 0:
            return Fraction(0)
        g, r = divmod(p - 1, m - 1)
        if r < m - 2:
            # interior 事件 = 孩子事件 r+1
            return g * d + child.prefix_duration(r + 1)
        # 休止起点：组 g 的尾部休止开始处
        return g * d + (d - child.last_dur)

    def _concat_prefix(self, p: int) -> Fraction:
        idx = bisect_right(self.slot_start, p) - 1
        slot = self.slots[idx]
        beat = self.slot_beat[idx]
        k_in = p - self.slot_start[idx]
        if slot.kind == TOKEN:
            return beat
        beat += slot.term.prefix_duration(slot.lo + k_in)
        if slot.lo:
            beat -= slot.term.prefix_duration(slot.lo)
        return beat
