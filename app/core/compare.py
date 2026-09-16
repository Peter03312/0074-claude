"""两个压缩序列的逐事件比对。

策略（保证只走结构步数量级，不物化 repeat）：

1. 每次先比较当前事件（音高/休止/时值）。
2. 若两侧当前位置都落在某个非合并 repeat 的副本起点，则整块跳过：
   副本长度分别为 m1、m2，联合周期 L = lcm(m1,m2)。只要两侧各自在
   runway 内还有 ≥2 个完整联合周期，就先实走一个联合周期确认相等，
   然后一次跳过其余整周期（jump = (floor(runway/L)-1)·L）。
3. 否则记录“下一位置 → 结构签名对”，重遇同一签名对时按周期跳过
   （覆盖合并 repeat、concat token 等结构）。
"""

from __future__ import annotations

from dataclasses import dataclass
from fractions import Fraction
from math import gcd

from app.models import MAX_COMPARE_STEPS

from .cursor import Cursor, CursorState
from .errors import SemanticError
from .terms import Term


@dataclass(slots=True)
class Difference:
    beat: Fraction
    expected: CursorState | None  # None 表示该侧提前结束
    handwritten: CursorState | None


def _lcm(a: int, b: int) -> int:
    return a // gcd(a, b) * b


def compare_terms(a: Term, b: Term) -> Difference | None:
    """返回第一处差异；完全一致（起音/音高/休止/时值）返回 None。"""
    ca, cb = Cursor(a, 0), Cursor(b, 0)
    seen: dict[tuple[tuple, tuple], int] = {}
    steps = 0

    while not ca.exhausted or not cb.exhausted:
        steps += 1
        if steps > MAX_COMPARE_STEPS:
            raise SemanticError(
                f"压缩比对结构步数超过上限 {MAX_COMPARE_STEPS}", ""
            )

        if ca.exhausted or cb.exhausted:
            beat = cb.beat() if ca.exhausted else ca.beat()
            sa = ca.peek() if not ca.exhausted else None
            sb = cb.peek() if not cb.exhausted else None
            return Difference(beat, sa, sb)

        sa, sb = ca.peek(), cb.peek()
        if not sa.event.same_music(sb.event):
            return Difference(ca.beat(), sa, sb)

        # 算术块跳转：两侧都位于 repeat 周期边界，且重复单元是同一（结构共享）
        # 孩子项，才能保证被跳过的副本逐事件相同。
        ra = ca.innermost_repeat_skip()
        rb = cb.innermost_repeat_skip()
        if ra is not None and rb is not None and ra[2] is rb[2]:
            m1, rw1, _ = ra
            m2, rw2, _ = rb
            joint = _lcm(m1, m2)
            blocks1 = rw1 // joint
            blocks2 = rw2 // joint
            nblocks = min(blocks1, blocks2)
            if nblocks >= 2:
                pos = ca.position()
                # 实走一个联合周期确认两侧相等（同一孩子 ⇒ 副本本身逐事件相同）
                ok, diff = _walk_equal(ca, cb, joint)
                if diff is not None:
                    return diff
                if not ok:
                    return diff
                # 跳过其余整周期，保留最后一个联合周期实走以捕获接缝/末尾差异
                skip = (nblocks - 1) * joint
                ca.seek(pos + skip + joint)
                cb.seek(pos + skip + joint)
                seen = {}
                continue

        ca.advance()
        cb.advance()
        if ca.exhausted and cb.exhausted:
            return None
        if ca.exhausted or cb.exhausted:
            continue

        # 签名兜底（合并 repeat / token / 一般结构）
        key = (ca.signature(), cb.signature())
        pos = ca.position()
        if key in seen:
            delta = pos - seen[key]
            if delta > 0:
                rem = ca.remaining()
                k = rem // delta
                if k >= 2:
                    new_pos = pos + (k - 1) * delta
                    ca.seek(new_pos)
                    cb.seek(new_pos)
                    seen = {}
                    continue
        else:
            seen[key] = pos

    return None


def _walk_equal(ca: Cursor, cb: Cursor, n: int) -> tuple[bool, Difference | None]:
    """从当前位置实走 n 个事件，确认两侧逐事件相等（不检测结束）。"""
    for _ in range(n):
        if ca.exhausted or cb.exhausted:
            beat = cb.beat() if ca.exhausted else ca.beat()
            sa = ca.peek() if not ca.exhausted else None
            sb = cb.peek() if not cb.exhausted else None
            return False, Difference(beat, sa, sb)
        sa, sb = ca.peek(), cb.peek()
        if not sa.event.same_music(sb.event):
            return False, Difference(ca.beat(), sa, sb)
        ca.advance()
        cb.advance()
    return True, None
