"""测试辅助：朴素完整展开参考实现 + 请求构造。

只用于测试；对事件数小的 DAG 直接物化，与生产压缩求值器做差分。
"""

from __future__ import annotations

from fractions import Fraction

from app.core.compiler import Compiler
from app.core.cursor import Cursor
from app.models import AnalyzeRequest

PITCH = 0
REST = None


def naive_events(nodes, root_id):
    """按题面语义朴素求值：返回 [(pitch_or_None, Fraction)]。"""
    by_id = {n["id"]: n for n in nodes}

    def ev(node_id):
        n = by_id[node_id]
        op = n["op"]
        if op == "phrase":
            out = []
            i = 0
            items = n["items"]
            while i < len(items):
                it = items[i]
                d = Fraction(it["duration"]["n"], it["duration"].get("d", 1))
                if it["type"] == "rest":
                    if out and out[-1][0] is REST:
                        out[-1] = (REST, out[-1][1] + d)
                    else:
                        out.append((REST, d))
                    i += 1
                else:
                    p = it["pitch"]
                    j = i
                    while j + 1 < len(items) and items[j].get("tie_next"):
                        nxt = items[j + 1]
                        assert nxt["type"] == "note" and nxt["pitch"] == p
                        d += Fraction(nxt["duration"]["n"], nxt["duration"].get("d", 1))
                        j += 1
                    out.append((p, d))
                    i = j + 1
            return out
        if op == "concat":
            seq = []
            for c in n["children"]:
                seq.extend(ev(c))
            return _merge_rests(seq)
        if op == "repeat":
            seq = ev(n["child"]) * n["count"]
            return _merge_rests(seq)
        if op == "transpose":
            return [(None if p is None else p + n["k"], d) for p, d in ev(n["child"])]
        if op == "pitch_mirror":
            axis = Fraction(n["axis"]["n"], n["axis"].get("d", 1))
            t = int(2 * axis)
            return [(None if p is None else t - p, d) for p, d in ev(n["child"])]
        if op == "reverse":
            return list(reversed(ev(n["child"])))
        if op == "stretch":
            q = Fraction(n["q"]["n"], n["q"].get("d", 1))
            return [(p, d * q) for p, d in ev(n["child"])]
        raise AssertionError(op)

    def merge_seq(seq):
        return _merge_rests(seq)

    return merge_seq(ev(root_id))


def _merge_rests(seq):
    out = []
    for p, d in seq:
        if p is REST and out and out[-1][0] is REST:
            out[-1] = (REST, out[-1][1] + d)
        else:
            out.append((p, d))
    return out


def compressed_events(nodes, root_id):
    req = AnalyzeRequest.model_validate(
        {"expected_root": root_id, "handwritten_root": root_id, "nodes": nodes}
    )
    c = Compiler(req.nodes)
    c.compile_all()
    t = c.term_for(root_id, "")
    cur = Cursor(t)
    out = []
    while not cur.exhausted:
        s = cur.peek()
        out.append((s.event.pitch, s.event.duration))
        cur.advance()
    return t, out


def make_request(nodes, ea, hb):
    return AnalyzeRequest.model_validate(
        {"expected_root": ea, "handwritten_root": hb, "nodes": nodes}
    )


def first_diff_naive(a, b):
    beat = Fraction(0)
    i = 0
    while i < len(a) or i < len(b):
        if i >= len(a) or i >= len(b):
            return beat, i
        if a[i] != b[i]:
            return beat, i
        beat += a[i][1]
        i += 1
    return None
