"""DAG 校验与压缩编译。

输入是已通过 Pydantic 结构校验的节点表；这里负责：

1. id 唯一、子引用存在、无环（Kahn 拓扑排序）；
2. phrase 的连音合法性与规范合并（连音 + 连续休止）；
3. transpose / pitch_mirror 越界（MIDI 0..127）与坏轴（分母不为 1/2）；
4. concat 跨边界休止合并的槽编译；
5. repeat 边界休止合并元数据；
6. 规模越界（节点数、基础项数、展开事件数 10^15）。

任何错误都以 SemanticError 抛出，loc 为 JSON Pointer，调用方整单 422。
"""

from __future__ import annotations

import heapq
from fractions import Fraction

from app.models import (
    MAX_BASE_ITEMS,
    MAX_EVENTS_EXPANDED,
    MAX_NODES,
    ConcatNode,
    PhraseNode,
    PitchMirrorNode,
    RepeatNode,
    StretchNode,
    TransposeNode,
)
from app.models import Node as NodeModel

from .errors import SemanticError, join
from .events import ChainStep, Event
from .terms import (
    BODY,
    CONCAT,
    EMPTY,
    PHRASE,
    PITCH,
    REPEAT,
    REVERSE,
    STRETCH,
    TOKEN,
    Slot,
    Term,
    step,
)

SINGLE_CHILD_OPS = ("repeat", "transpose", "pitch_mirror", "reverse", "stretch")


def frac(num: int, den: int = 1) -> Fraction:
    if den <= 0:  # Pydantic 已拦截，这里仅防御
        raise SemanticError("分母必须为正整数", "")
    return Fraction(num, den)


class Compiler:
    def __init__(self, nodes: list[NodeModel]):
        self.nodes = nodes
        self.by_id: dict[int, NodeModel] = {}
        self.terms: dict[int, Term] = {}

    # ---------- 顶层 ----------
    def compile_all(self) -> None:
        if len(self.nodes) > MAX_NODES:
            raise SemanticError(f"节点数超过 {MAX_NODES}", "/nodes")

        for idx, n in enumerate(self.nodes):
            if n.id in self.by_id:
                raise SemanticError(f"节点编号 {n.id} 重复", f"/nodes/{idx}/id")
            self.by_id[n.id] = n

        for idx in self._topo_order():
            self._compile_node(idx)

    def _topo_order(self) -> list[int]:
        """孩子先于父亲；同层按 nodes 数组下标递增（结果稳定、错误路径可预期）。"""
        position = {n.id: i for i, n in enumerate(self.nodes)}
        indeg: dict[int, int] = {n.id: 0 for n in self.nodes}
        radj: dict[int, list[int]] = {n.id: [] for n in self.nodes}

        for idx, n in enumerate(self.nodes):
            for dep in self._deps_of(n, idx):
                indeg[n.id] += 1
                radj[dep].append(n.id)

        heap = [position[nid] for nid, d in indeg.items() if d == 0]
        heapq.heapify(heap)
        result: list[int] = []
        while heap:
            idx = heapq.heappop(heap)
            nid = self.nodes[idx].id
            result.append(idx)
            for parent in radj[nid]:
                indeg[parent] -= 1
                if indeg[parent] == 0:
                    heapq.heappush(heap, position[parent])

        if len(result) != len(self.nodes):
            cyc = sorted(position[nid] for nid, d in indeg.items() if d > 0)
            loc = f"/nodes/{cyc[0]}" if cyc else "/nodes"
            raise SemanticError("节点引用成环", loc)
        return result

    def _deps_of(self, n: NodeModel, idx: int) -> list[int]:
        loc = f"/nodes/{idx}"
        if isinstance(n, ConcatNode):
            deps: list[int] = []
            for j, dep in enumerate(n.children):
                if dep not in self.by_id:
                    raise SemanticError(f"未知子节点 {dep}", join(join(loc, "children"), j))
                deps.append(dep)
            return deps
        if n.op in SINGLE_CHILD_OPS:
            if n.child not in self.by_id:
                raise SemanticError(f"未知子节点 {n.child}", join(loc, "child"))
            return [n.child]
        return []

    # ---------- 单节点 ----------
    def _compile_node(self, idx: int) -> None:
        n = self.nodes[idx]
        loc = f"/nodes/{idx}"
        if n.op == "phrase":
            term = self._compile_phrase(n, loc)
        elif n.op == "concat":
            term = self._compile_concat(n, loc)
        elif n.op == "repeat":
            term = self._compile_repeat(n, loc)
        elif n.op == "transpose":
            term = self._compile_transpose(n, loc)
        elif n.op == "pitch_mirror":
            term = self._compile_mirror(n, loc)
        elif n.op == "reverse":
            term = self._compile_reverse(n, loc)
        else:  # stretch
            term = self._compile_stretch(n, loc)
        self.terms[n.id] = term

    # ---- phrase ----
    def _compile_phrase(self, n: PhraseNode, loc: str) -> Term:
        if len(n.items) > MAX_BASE_ITEMS:
            raise SemanticError(f"基础乐句项数超过 {MAX_BASE_ITEMS}", join(loc, "items"))
        t = Term(PHRASE, n.id)
        events: list[Event] = []
        idxs: list[int] = []
        items = n.items
        items_loc = join(loc, "items")

        i = 0
        last = len(items) - 1
        # 预校验：任何 tie_next 都必须有紧邻的下一项；最后一项标 tie_next 非法。
        for i0, it0 in enumerate(items):
            if it0.type == "note" and it0.tie_next and i0 == last:
                raise SemanticError(
                    "连音标记后必须紧邻一个同音高音符（最后一个音符不能连音）",
                    join(items_loc, i0),
                )
        while i < len(items):
            it = items[i]
            if it.type == "note":
                dur = frac(it.duration.n, it.duration.d)
                if dur <= 0:
                    raise SemanticError("时值必须为正", join(items_loc, i))
                pitch = it.pitch
                j = i
                while (
                    j + 1 < len(items)
                    and items[j].type == "note"
                    and items[j].tie_next
                ):
                    nxt = items[j + 1]
                    if nxt.type != "note" or nxt.pitch != pitch:
                        raise SemanticError(
                            "连音只能连接紧邻的同音高音符", join(items_loc, j + 1)
                        )
                    nd = frac(nxt.duration.n, nxt.duration.d)
                    if nd <= 0:
                        raise SemanticError("时值必须为正", join(items_loc, j + 1))
                    dur += nd
                    j += 1
                # 合并事件取演奏顺序最早贡献项 i
                events.append(Event(pitch, dur))
                idxs.append(i)
                i = j + 1
            else:
                dur = frac(it.duration.n, it.duration.d)
                if dur <= 0:
                    raise SemanticError("时值必须为正", join(items_loc, i))
                if events and events[-1].is_rest:
                    events[-1] = Event(None, events[-1].duration + dur)
                else:
                    events.append(Event(None, dur))
                    idxs.append(i)
                i += 1

        t.events = tuple(events)
        t.eidx = tuple(idxs)
        t.count = len(events)
        total = Fraction(0)
        prefix = [Fraction(0)]
        for e in events:
            total += e.duration
            prefix.append(total)
        t.eprefix = tuple(prefix)
        t.duration = total
        notes = [e.pitch for e in events if not e.is_rest]
        if notes:
            t.pmin = min(notes)
            t.pmax = max(notes)
        if events:
            t.first_rest = events[0].is_rest
            t.last_rest = events[-1].is_rest
            t.first_dur = events[0].duration
            t.last_dur = events[-1].duration
            t.first_rel = (step(n.id, item=idxs[0]),)
            t.last_rel = (step(n.id, item=idxs[-1]),)
        return t

    # ---- concat ----
    def _compile_concat(self, n: ConcatNode, loc: str) -> Term:
        parts = [self.terms[c] for c in n.children]
        t = Term(CONCAT, n.id)

        slots: list[Slot] = []
        slot_beat: list[Fraction] = []
        slot_start: list[int] = []
        total_count = 0
        total_dur = Fraction(0)

        def emit(slot: Slot) -> None:
            nonlocal total_count, total_dur
            slot_start.append(total_count)
            slot_beat.append(total_dur)
            total_count += slot.length
            if slot.kind == BODY:
                total_dur += slot.term.prefix_duration(slot.hi) - slot.term.prefix_duration(
                    slot.lo
                )
            else:
                total_dur += slot.duration
            slots.append(slot)

        # 统一算法：把各非空部分看成 [头休止?][主体][尾休止?]，按顺序拼接。
        # 维护一个“开放休止” open_rest：它是上一部分留下的尾休止（或合并链），
        # 必然在演奏顺序上位于“下一部分主体之前”。
        #   - 下一部分以头休止开始：头休止并入 open_rest，随后接其主体；
        #   - 下一部分不以头休止开始：先把 open_rest 作为 token 发射；
        #   - 下一部分若整体就是一个休止：只并入 open_rest（不改变来源）。
        open_dur: Fraction | None = None
        open_path: tuple[ChainStep, ...] = ()

        def flush_open() -> None:
            nonlocal open_dur, open_path
            if open_dur is not None:
                emit(Slot(TOKEN, 1, duration=open_dur, path=open_path))
                open_dur, open_path = None, ()

        for part in parts:
            if part.empty:
                continue
            starts_rest = part.first_rest
            ends_rest = part.last_rest

            if part.count == 1 and starts_rest:
                # 纯休止部分：并入开放休止；来源保持更早贡献项
                if open_dur is None:
                    open_dur = part.first_dur
                    open_path = part.first_rel
                else:
                    open_dur += part.first_dur
                continue

            # 多事件部分。其主体（去掉可能的头/尾休止）之前必须先落定 open rest：
            #   * 当前以头休止开始 → 头休止与 open 合并（接缝两侧皆休止）；
            #   * 否则 open rest 在主体之前作为独立 token 发射。
            if starts_rest:
                if open_dur is None:
                    open_dur = part.first_dur
                    open_path = part.first_rel
                else:
                    open_dur += part.first_dur
                body_lo = 1
            else:
                flush_open()
                body_lo = 0

            body_end = part.count - 1 if ends_rest else part.count
            # open rest（合并了头休止）必须排在主体之前
            flush_open()
            if body_lo < body_end:
                emit(Slot(BODY, body_end - body_lo, term=part, lo=body_lo, hi=body_end))

            if ends_rest:
                # 尾休止成为新的开放休止，等待与下一部分合并
                open_dur = part.last_dur
                open_path = part.last_rel

        flush_open()

        t.slots = tuple(slots)
        t.slot_beat = tuple(slot_beat)
        t.slot_start = tuple(slot_start)
        t.count = total_count
        t.duration = total_dur
        self._set_concat_endpoints(n.id, t, parts, slots)
        self._guard_count(n.id, t, loc)
        return t

    def _set_concat_endpoints(
        self, node_id: int, t: Term, parts: list[Term], slots: list[Slot]
    ) -> None:
        nonempty = [p for p in parts if not p.empty]
        if not nonempty:
            return

        def first_of(slot: Slot) -> tuple[Fraction, tuple[ChainStep, ...], bool]:
            if slot.kind == TOKEN:
                return slot.duration, slot.path, True
            return slot.term.first_dur, slot.term.first_rel, slot.term.first_rest

        def last_of(slot: Slot) -> tuple[Fraction, tuple[ChainStep, ...], bool]:
            if slot.kind == TOKEN:
                return slot.duration, slot.path, True
            return slot.term.last_dur, slot.term.last_rel, slot.term.last_rest

        own = (step(node_id),)
        f_dur, f_path, f_rest = first_of(slots[0])
        l_dur, l_path, l_rest = last_of(slots[-1])
        t.first_dur, t.last_dur = f_dur, l_dur
        t.first_rest, t.last_rest = f_rest, l_rest
        t.first_rel = own + f_path
        t.last_rel = own + l_path

        mins = [p.pmin for p in nonempty if p.pmin is not None]
        maxs = [p.pmax for p in nonempty if p.pmax is not None]
        if mins:
            t.pmin = min(mins)
            t.pmax = max(maxs)

    # ---- repeat ----
    def _compile_repeat(self, n: RepeatNode, loc: str) -> Term:
        child = self.terms[n.child]
        if n.count == 0 or child.empty:
            return Term(EMPTY, n.id)
        t = Term(REPEAT, n.id)
        t.child = child
        t.rep_c = n.count
        m = child.count
        t.rep_m = m
        t.duration = n.count * child.duration
        merges = child.first_rest and child.last_rest

        if not merges:
            t.count = n.count * m
        elif m == 1:
            t.count = 1
        else:
            t.count = (n.count - 1) * (m - 1) + m

        t.pmin, t.pmax = child.pmin, child.pmax
        t.first_rest, t.last_rest = child.first_rest, child.last_rest
        t.first_dur, t.last_dur = child.first_dur, child.last_dur
        if merges and m == 1:
            # 唯一事件是 c 个相同休止合并而成，时长为 c*F
            merged = n.count * child.first_dur
            t.first_dur = t.last_dur = merged
        t.first_rel = (ChainStep(n.id, 0),) + child.first_rel
        last_i = 0 if (merges and m == 1) else n.count - 1
        t.last_rel = (ChainStep(n.id, last_i),) + child.last_rel
        self._guard_count(n.id, t, loc)
        return t

    # ---- 音高变换 ----
    def _compile_transpose(self, n: TransposeNode, loc: str) -> Term:
        child = self.terms[n.child]
        if child.pmin is not None:
            lo, hi = child.pmin + n.k, child.pmax + n.k
            if lo < 0 or hi > 127:
                raise SemanticError(
                    f"移调 {n.k:+d} 使音高越出 MIDI 0..127", join(loc, "k")
                )
        t = self._wrap_simple(PITCH, n.id, child)
        t.s, t.t = 1, n.k
        if child.pmin is not None:
            t.pmin = child.pmin + n.k
            t.pmax = child.pmax + n.k
        return t

    def _compile_mirror(self, n: PitchMirrorNode, loc: str) -> Term:
        axis = frac(n.axis.n, n.axis.d)
        if axis.denominator not in (1, 2):
            raise SemanticError(
                "镜像轴必须为整数或半整数（分母仅可为 1 或 2）", join(loc, "axis")
            )
        child = self.terms[n.child]
        tval_num = 2 * axis.numerator
        den = axis.denominator
        # 轴分母为 1 或 2 时 2*axis 恒为整数
        tval = tval_num // den
        if child.pmin is not None:
            # 镜像翻转音高顺序：新最小来自旧最大、新最大来自旧最小
            lo, hi = tval - child.pmax, tval - child.pmin
            if lo < 0 or hi > 127:
                raise SemanticError(
                    f"以 {axis} 为轴镜像使音高越出 MIDI 0..127", join(loc, "axis")
                )
        t = self._wrap_simple(PITCH, n.id, child)
        t.s, t.t = -1, tval
        if child.pmin is not None:
            # 镜像翻转音高顺序：新最小来自旧最大（先取局部变量，避免自覆盖）
            cmin, cmax = child.pmin, child.pmax
            t.pmin = tval - cmax
            t.pmax = tval - cmin
        return t

    # ---- 其他包装 ----
    def _compile_reverse(self, n: NodeModel, loc: str) -> Term:
        child = self.terms[n.child]
        t = self._wrap_simple(REVERSE, n.id, child)
        if child.empty:
            return t
        t.first_rest, t.last_rest = child.last_rest, child.first_rest
        t.first_dur, t.last_dur = child.last_dur, child.first_dur
        # 反转后的相对路径在 Cursor/rel_path 中按位置即时计算，端点例外：
        t.first_rel = (step(n.id),) + child.last_rel
        t.last_rel = (step(n.id),) + child.first_rel
        return t

    def _compile_stretch(self, n: StretchNode, loc: str) -> Term:
        q = frac(n.q.n, n.q.d)
        if q <= 0:
            raise SemanticError("stretch 倍率必须为正有理数", join(loc, "q"))
        child = self.terms[n.child]
        t = self._wrap_simple(STRETCH, n.id, child)
        t.q = q
        t.duration = q * child.duration
        if not child.empty:
            t.first_dur = q * child.first_dur
            t.last_dur = q * child.last_dur
        return t

    def _wrap_simple(self, kind: str, node_id: int, child: Term) -> Term:
        t = Term(kind, node_id)
        t.child = child
        t.count = child.count
        t.duration = child.duration
        t.first_rest, t.last_rest = child.first_rest, child.last_rest
        t.first_dur, t.last_dur = child.first_dur, child.last_dur
        t.first_rel, t.last_rel = child.first_rel, child.last_rel
        t.pmin, t.pmax = child.pmin, child.pmax
        return t

    # ---------- 通用 ----------
    def _guard_count(self, node_id: int, t: Term, loc: str) -> None:
        if t.count > MAX_EVENTS_EXPANDED:
            raise SemanticError(f"节点 {node_id} 规范事件数超过上限 10^15", loc)

    def term_for(self, root_id: int, loc: str) -> Term:
        if root_id not in self.by_id:
            raise SemanticError(f"未知根节点 {root_id}", loc)
        return self.terms[root_id]
