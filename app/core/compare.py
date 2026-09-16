"""两个压缩序列的逐事件比对。

加速必须**两侧各自独立成立**，绝不允许用一侧的周期去推断另一侧：

1. 每次先比较当前事件（音高/休止/时值）。
2. **算术块跳转**：两侧都位于 repeat 周期边界、且重复单元为同一（结构共享）
   孩子项时，取两侧副本长度的 LCM 为联合周期，先实走一个联合周期确认
   相等，再按双方各自“到下一个非无缝接缝/序列尾”的剩余量，跳过共同允许的
   整周期数（取两侧 min）。
3. **独立签名周期兜底**：对每个游标分别维护“结构签名 -> 位置”。只有当
   **两侧在当前位置都重遇了自己的签名**（各自独立呈现周期性）时才跳过，
   跳转量取两侧周期与各自剩余量的共同最小值，因此永远不会越过较短一侧的
   结束点。这也覆盖合并休止 repeat、concat token 等一般周期结构。
"""

from __future__ import annotations

from dataclasses import dataclass
from math import gcd

from app.models import MAX_COMPARE_STEPS

from .cursor import Cursor, CursorState
from .errors import SemanticError
from .terms import Term


@dataclass(slots=True)
class Difference:
    beat: object
    expected: CursorState | None  # None 表示该侧提前结束
    handwritten: CursorState | None


def _lcm(a: int, b: int) -> int:
    return a // gcd(a, b) * b


def compare_terms(a: Term, b: Term) -> Difference | None:
    """返回第一处差异；完全一致（起音/音高/休止/时值）返回 None。"""
    ca, cb = Cursor(a, 0), Cursor(b, 0)
    seen_a: dict[tuple, int] = {}
    seen_b: dict[tuple, int] = {}
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

        # 算术块跳转：两侧 repeat 周期边界且重复单元是同一结构共享孩子。
        ra = ca.innermost_repeat_skip()
        rb = cb.innermost_repeat_skip()
        if ra is not None and rb is not None and ra[2] is rb[2]:
            m1, rw1, _ = ra
            m2, rw2, _ = rb
            joint = _lcm(m1, m2)
            nblocks = min(rw1 // joint, rw2 // joint)
            if nblocks >= 2:
                pos = ca.position()
                ok, diff = _walk_equal(ca, cb, joint)
                if diff is not None:
                    return diff
                if not ok:
                    return diff
                # 双方各自只跳过共同允许的整周期数，保留最后一个联合周期实走
                skip = (nblocks - 1) * joint
                ca.seek(pos + skip + joint)
                cb.seek(pos + skip + joint)
                seen_a, seen_b = {}, {}
                continue

        ca.advance()
        cb.advance()
        if ca.exhausted and cb.exhausted:
            return None
        if ca.exhausted or cb.exhausted:
            continue

        # 独立签名周期兜底：两侧各自重遇自己的签名才跳过。
        pos = ca.position()
        sa_sig, sb_sig = ca.signature(), cb.signature()
        jump = 0
        prev_a = seen_a.get(sa_sig)
        prev_b = seen_b.get(sb_sig)
        if prev_a is not None and prev_b is not None:
            da, db = pos - prev_a, pos - prev_b
            # 两侧周期相同（位置始终同步，da==db），并保留至少一个周期实走
            period = min(da, db)
            k = min(ca.remaining(), cb.remaining()) // period
            if period > 0 and k >= 2:
                jump = (k - 1) * period
        if jump:
            ca.seek(pos + jump)
            cb.seek(pos + jump)
            seen_a, seen_b = {}, {}
        else:
            seen_a.setdefault(sa_sig, pos)
            seen_b.setdefault(sb_sig, pos)

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
