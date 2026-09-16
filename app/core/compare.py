"""两个压缩序列的逐事件比对。

加速必须**先验证、后跳过**，且任何跳过都不能越过较短一侧的结束点或
repeat 的连续周期区间：

1. 每次先比较当前事件（音高/休止/时值）。
2. **算术块跳转**：两侧都位于 repeat 周期边界、且重复单元为同一（结构共享）
   孩子项时，先实走一个 LCM 联合周期验证，再跳过共同允许的整周期数。
3. **签名周期兜底**：对每个游标分别维护“结构签名 -> 位置”。当两侧在当前
   位置都重遇自己的签名时，各自从锚点起在当前 repeat 区间内严格周期；
   但签名重复**不能保证两侧彼此相等**，所以必须从当前位置先实走验证一整个
   联合周期，逐事件相等才跳过，并把跳过量限制在各自周期区间剩余量内。
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


def _difference(ca: Cursor, cb: Cursor) -> Difference:
    """构造差异：任一侧结束时另一侧给出当前事件。"""
    if ca.exhausted:
        beat = cb.beat()
        sa, sb = None, cb.peek()
    else:
        beat = ca.beat()
        sa, sb = ca.peek(), (None if cb.exhausted else cb.peek())
    return Difference(beat, sa, sb)


def compare_terms(a: Term, b: Term) -> Difference | None:
    """返回第一处差异；完全一致（起音/音高/休止/时值）返回 None。"""
    ca, cb = Cursor(a, 0), Cursor(b, 0)
    # 每个相位签名首次出现的位置；据此识别“当前位置是否为某周期的整数倍”。
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
            return _difference(ca, cb)

        sa, sb = ca.peek(), cb.peek()
        if not sa.event.same_music(sb.event):
            return Difference(ca.beat(), sa, sb)

        pos = ca.position()

        # (A) 算术块跳转：同一结构共享孩子的 repeat。
        ra = ca.innermost_repeat_skip()
        rb = cb.innermost_repeat_skip()
        if ra is not None and rb is not None and ra[2] is rb[2]:
            joint = _lcm(ra[0], rb[0])
            nblocks = min(ra[1] // joint, rb[1] // joint)
            if nblocks >= 2:
                ok, diff = _walk_equal(ca, cb, joint)
                if diff is not None:
                    return diff
                if not ok:
                    return diff
                skip = (nblocks - 1) * joint
                ca.seek(pos + joint + skip)
                cb.seek(pos + joint + skip)
                seen_a, seen_b = {}, {}
                continue

        # (B) 签名周期兜底：判断“当前位置是否为两侧各自周期的整数倍”。
        # 对每一侧，当前签名在位置 origin（< pos）首次出现且
        # (pos-origin) 是相位周期 ⇒ pos 是该侧某个周期的整数倍边界。
        sig_a, sig_b = ca.signature(), cb.signature()
        origin_a = seen_a.get(sig_a)
        origin_b = seen_b.get(sig_b)
        jumped = False
        if origin_a is not None and origin_b is not None:
            period = _lcm(pos - origin_a, pos - origin_b)
            rwa, rwb = ca.periodic_runway(), cb.periodic_runway()
            if (
                period > 0
                and rwa is not None
                and rwb is not None
                and period <= rwa
                and period <= rwb
            ):
                # 先从当前位置实走一整个联合周期验证；窗口内任何差异
                # （包括两侧周期不同造成的错位）都会被立即捕获。
                ok, diff = _walk_equal(ca, cb, period)
                if diff is not None:
                    return diff
                if ok:
                    left = min(ca.periodic_runway() or 0, cb.periodic_runway() or 0)
                    nfull = left // period
                    if nfull >= 2:
                        skip = (nfull - 1) * period
                        ca.seek(pos + period + skip)
                        cb.seek(pos + period + skip)
                        seen_a, seen_b = {}, {}
                        jumped = True
        if not jumped:
            seen_a.setdefault(sig_a, pos)
            seen_b.setdefault(sig_b, pos)
            ca.advance()
            cb.advance()

    return None


def _walk_equal(ca: Cursor, cb: Cursor, n: int) -> tuple[bool, Difference | None]:
    """从当前位置实走 n 个事件，确认两侧逐事件相等。

    成功时游标停在“下一事件”位置（已前进 n 步）；失败时立即返回第一处差异。
    """
    for _ in range(n):
        if ca.exhausted or cb.exhausted:
            return False, _difference(ca, cb)
        sa, sb = ca.peek(), cb.peek()
        if not sa.event.same_music(sb.event):
            return False, Difference(ca.beat(), sa, sb)
        ca.advance()
        cb.advance()
    return True, None
