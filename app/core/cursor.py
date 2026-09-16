"""压缩事件游标。

Cursor 只保存“根项内的零基绝对事件位置” p 与一条下钻帧栈。前进即
``seek(p+1)``，每帧的子位置都由父项的纯算术映射得到，因此没有任何可
失配的增量状态；repeat 永不物化，万亿事件只产生 O(结构规模) 个不同位置。

repeat 合并模式（孩子首尾都是休止）概念序列，记孩子事件数 m、首休止 F、
尾部休止 T、interior = 孩子事件 1..m-2：

    F, (I1..I_{m-2}, Rest) 重复 (c-1) 次, (I1..I_{m-2}), T

每组（除开头）占 m-1 个输出位置；边界 Rest 时长 F+T，最终尾部 T。
"""

from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from fractions import Fraction

from .events import ChainStep, Event
from .terms import (
    BODY,
    CONCAT,
    PHRASE,
    PITCH,
    REPEAT,
    REVERSE,
    STRETCH,
    Term,
)

# repeat 映射到孩子事件 / 合成休止
KIND_CHILD = "child"
KIND_SYNTH = "synth"


@dataclass(slots=True)
class Frame:
    term: Term
    p: int  # 项内绝对输出位置


@dataclass(slots=True)
class Leaf:
    """叶子：phrase 事件、concat token 或 repeat 合成休止。"""

    pitch: int | None
    raw_duration: Fraction
    path: tuple[ChainStep, ...]  # 从“最近的 repeat/concat 节点步之后”到叶子
    kind: str = "phrase"  # phrase | token | synth


class Cursor:
    __slots__ = ("leaf", "p", "root", "stack")

    def __init__(self, root: Term, p: int = 0):
        self.root = root
        self.p = 0
        self.stack: list[Frame] = []
        self.leaf: Leaf | None = None
        self.seek(p)

    # ---------- repeat 映射 ----------
    @staticmethod
    def _repeat_map(term: Term, p: int):
        """返回 (KIND_CHILD, child_index) 或 (KIND_SYNTH, synth_info)。"""
        m = term.rep_m
        child = term.child
        merges = child.first_rest and child.last_rest
        if not merges:
            i, t = divmod(p, m)
            return KIND_CHILD, t, i
        if m == 1:
            # 唯一事件：c 份相同休止合并
            return KIND_SYNTH, 0, {"iteration": 0, "final": True, "m": 1}
        if p == 0:
            return KIND_CHILD, 0, 0
        g, r = divmod(p - 1, m - 1)
        if r < m - 2:
            return KIND_CHILD, r + 1, g
        return KIND_SYNTH, g, {"iteration": None, "final": g >= term.rep_c - 1, "m": m}

    # ---------- 下钻 ----------
    def seek(self, p: int) -> None:
        self.p = p
        self.stack = []
        self.leaf = None
        if 0 <= p < self.root.count:
            self.leaf = self._descend(self.root, p)

    def _descend(self, term: Term, p: int) -> Leaf:
        k = term.kind
        if k == PHRASE:
            self.stack.append(Frame(term, p))
            ev = term.events[p]
            path = (ChainStep(term.node_id, item_index=term.eidx[p]),)
            return Leaf(ev.pitch, ev.duration, path, "phrase")

        if k in (REVERSE, STRETCH, PITCH):
            self.stack.append(Frame(term, p))
            cp = term.count - 1 - p if k == REVERSE else p
            return self._descend(term.child, cp)

        if k == REPEAT:
            self.stack.append(Frame(term, p))
            kind, idx, info = self._repeat_map(term, p)
            if kind == KIND_CHILD:
                return self._descend(term.child, idx)
            # 合成休止
            c = term.rep_c
            child = term.child
            m = info["m"]
            if m == 1:
                duration = c * child.first_dur
                iteration = 0
                suffix = child.last_rel
            else:
                g = idx
                if info["final"]:
                    duration = child.last_dur
                    iteration = c - 1
                else:
                    duration = child.first_dur + child.last_dur
                    iteration = g  # 边界休止取较早副本 g 的尾部
                suffix = child.last_rel
            path = (ChainStep(term.node_id, iteration=iteration),) + suffix
            return Leaf(None, duration, path, "synth")

        if k == CONCAT:
            si = bisect_right(term.slot_start, p) - 1
            self.stack.append(Frame(term, p))
            slot = term.slots[si]
            if slot.kind == BODY:
                child_p = slot.lo + (p - term.slot_start[si])
                return self._descend(slot.term, child_p)
            return Leaf(None, slot.duration, slot.path, "token")

        raise AssertionError(k)

    # ---------- 查询 ----------
    @property
    def exhausted(self) -> bool:
        return self.leaf is None

    def position(self) -> int:
        return self.p if self.leaf is not None else self.root.count

    def remaining(self) -> int:
        return self.root.count - self.position()

    def beat(self) -> Fraction:
        if self.leaf is None:
            return self.root.duration
        return self.root.prefix_duration(self.p)

    def peek(self) -> CursorState:
        leaf = self.leaf
        qprod = Fraction(1)
        s, t = 1, 0
        chain: list[ChainStep] = []

        # synth 叶子自带最近 repeat 节点步；该帧不再重复输出
        synth = leaf.kind == "synth"
        nframes = len(self.stack)
        # 来源链自根向叶；音高/时值变换自叶向根累积（仿射复合顺序）
        for d, fr in enumerate(self.stack):
            k = fr.term.kind
            if synth and d == nframes - 1:
                continue  # synth 的 repeat 步已在 leaf.path
            if k == REPEAT:
                _, _, info = self._repeat_map(fr.term, fr.p)
                iteration = info if isinstance(info, int) else info["iteration"]
                chain.append(ChainStep(fr.term.node_id, iteration=iteration))
            elif k in (CONCAT, REVERSE, STRETCH, PITCH):
                chain.append(ChainStep(fr.term.node_id))
            # PHRASE 帧步由 leaf.path 提供

        for fr in reversed(self.stack):
            k = fr.term.kind
            if k == STRETCH:
                qprod *= fr.term.q
            elif k == PITCH:
                s, t = fr.term.s * s, fr.term.s * t + fr.term.t

        chain.extend(leaf.path)

        dur = leaf.raw_duration * qprod
        event = Event(None, dur) if leaf.pitch is None else Event(s * leaf.pitch + t, dur)
        return CursorState(event=event, chain=tuple(chain))

    def advance(self) -> None:
        if self.leaf is None:
            return
        nxt = self.p + 1
        if nxt >= self.root.count:
            self.seek(self.root.count)  # 清空 → 结束
        else:
            self.seek(nxt)

    # ---------- 周期签名 ----------
    def signature(self) -> tuple:
        """结构签名：含 repeat 副本内位置，**不含**单调递增的副本号/绝对 p。

        两位置签名相同 ⇒ 从此处起的事件流（音高、时值、来源节点结构）逐事件
        相同，可按周期整体跳过。
        """
        sig: list[tuple] = []
        for fr in self.stack:
            k = fr.term.kind
            if k == REPEAT:
                sig.append((REPEAT, fr.term.node_id, self._repeat_phase(fr.term, fr.p)))
            elif k == CONCAT:
                si = bisect_right(fr.term.slot_start, fr.p) - 1
                slot = fr.term.slots[si]
                if slot.kind == BODY:
                    local = fr.p - fr.term.slot_start[si]
                    sig.append((CONCAT, fr.term.node_id, si, "b", slot.lo + local))
                else:
                    sig.append((CONCAT, fr.term.node_id, si, "t"))
            elif k in (REVERSE, STRETCH, PITCH):
                sig.append((k, fr.term.node_id))
            elif k == PHRASE:
                sig.append((PHRASE, fr.term.node_id, fr.p))
        if self.leaf is not None and self.leaf.kind == "synth":
            sig.append(("synth",))
        return tuple(sig)

    @staticmethod
    def _repeat_phase(term: Term, p: int):
        """repeat 副本内相位（忽略副本号）。"""
        m = term.rep_m
        merges = term.child.first_rest and term.child.last_rest
        if not merges:
            return ("c", p % m)
        if m == 1:
            return ("s",)
        if p == 0:
            return ("f",)
        g, r = divmod(p - 1, m - 1)
        if r < m - 2:
            return ("i", r + 1)
        return ("t", g >= term.rep_c - 1)

    # ---------- 算术块跳转 ----------
    def innermost_repeat_skip(self) -> tuple[int, int, Term] | None:
        """若当前位置位于某个 repeat 的“周期边界”，返回 (period, runway, child)。

        非合并 repeat：周期 = m（一个完整副本），边界 p % m == 0。
        合并 repeat（首尾皆休止）：每个副本占 m-1 个输出位置（首休止 F 只在
        全曲开头一次），其后事件按 m-1 周期；边界即“内部音符位置”
        p>=2 且 (p-2) % (m-1) == 0。

        runway 为从当前位置到最近非无缝接缝（concat 槽边界）或序列尾的事件数。
        返回的 child 是该 repeat 的孩子项，调用方据此判断两侧重复单元是否
        为同一（结构共享的）子图——只有同一孩子才能保证副本逐事件相同。
        """
        idx = None
        period = 0
        child_term: Term | None = None
        for d in range(len(self.stack) - 1, -1, -1):
            fr = self.stack[d]
            if fr.term.kind != REPEAT:
                continue
            child = fr.term.child
            merges = child.first_rest and child.last_rest
            m = fr.term.rep_m
            if not merges:
                if fr.p % m == 0:
                    idx, period, child_term = d, m, child
                    break
                return None
            # 合并 repeat
            if m == 1:
                return None  # 只有一个休止，无可跳周期
            inner = m - 2
            if inner >= 1 and fr.p >= 2 and (fr.p - 2) % (m - 1) == 0:
                idx, period, child_term = d, m - 1, child
                break
            return None
        if idx is None:
            return None

        term = self.stack[idx].term
        rem = term.count - self.stack[idx].p

        for d in range(idx - 1, -1, -1):
            fr = self.stack[d]
            k = fr.term.kind
            if k in (REVERSE, STRETCH, PITCH):
                continue  # 双射/线性包装，接缝透明
            if k == REPEAT:
                child = fr.term.child
                merges = child.first_rest and child.last_rest
                if merges:
                    break
                rem = fr.term.count - fr.p
                continue
            if k == CONCAT:
                si = bisect_right(fr.term.slot_start, fr.p) - 1
                slot_end = fr.term.slot_start[si] + fr.term.slots[si].length
                rem = min(rem, slot_end - fr.p)
                break
            break
        return period, rem, child_term

    def periodic_runway(self) -> int | None:
        """当前位置在“最近 repeat 连续周期区间”内还剩多少事件（含当前事件）。

        签名重遇只能保证在同一个 repeat 的连续输出区间内逐事件周期重复；
        一旦跨过该 repeat 的末尾（进入 concat 的下一槽或外层后续结构），
        周期不再成立。返回 None 表示当前不在任何 repeat 区间内（不允许
        仅凭签名跳转）。
        """
        if self.leaf is None:
            return None
        deepest = None
        for d in range(len(self.stack) - 1, -1, -1):
            if self.stack[d].term.kind == REPEAT:
                deepest = d
                break
        if deepest is None:
            return None

        rem: int | None = None
        for d in range(deepest, -1, -1):
            fr = self.stack[d]
            k = fr.term.kind
            if k == REPEAT:
                here = fr.term.count - fr.p
                rem = here if rem is None else min(rem, here)
            elif k in (REVERSE, STRETCH, PITCH):
                continue
            elif k == CONCAT:
                si = bisect_right(fr.term.slot_start, fr.p) - 1
                slot_end = fr.term.slot_start[si] + fr.term.slots[si].length
                rem = min(rem, slot_end - fr.p)
                break
            else:
                break
        return rem


@dataclass(slots=True)
class CursorState:
    event: Event
    chain: tuple[ChainStep, ...]
