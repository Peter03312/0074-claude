"""聚焦回归：不同长度重复旋律 + 开头若干音相同，第一差异定位必须精确。

每个用例都与朴素完整展开对照；重复计数取到数千（足以进入签名/周期跳转路径，
又仍可朴素物化）。
"""

from __future__ import annotations

import itertools
import random

import pytest

from app.core.compare import compare_terms
from app.core.compiler import Compiler
from app.models import AnalyzeRequest

from .naive import first_diff_naive, naive_events


def compile_pair(nodes, ea, hb):
    req = AnalyzeRequest.model_validate(
        {"expected_root": ea, "handwritten_root": hb, "nodes": nodes}
    )
    c = Compiler(req.nodes)
    c.compile_all()
    return c.term_for(ea, ""), c.term_for(hb, "")


def assert_naive(nodes, ea, hb):
    a, b = compile_pair(nodes, ea, hb)
    A = naive_events(nodes, ea)
    B = naive_events(nodes, hb)
    nd = first_diff_naive(A, B)
    d = compare_terms(a, b)
    if nd is None:
        assert d is None, nodes
        return
    beat, idx = nd
    assert d is not None, nodes
    assert d.beat == beat, (beat, d.beat, nodes)
    if idx < len(A):
        assert (d.expected.event.pitch, d.expected.event.duration) == A[idx]
    else:
        assert d.expected is None
    if idx < len(B):
        assert (d.handwritten.event.pitch, d.handwritten.event.duration) == B[idx]
    else:
        assert d.handwritten is None


def repeat_pair_nodes(ua, ub, ca, cb):
    return [
        {"id": 0, "op": "phrase",
         "items": [{"type": "note", "pitch": p, "duration": {"n": 1}} for p in ua]},
        {"id": 1, "op": "phrase",
         "items": [{"type": "note", "pitch": p, "duration": {"n": 1}} for p in ub]},
        {"id": 2, "op": "repeat", "child": 0, "count": ca},
        {"id": 3, "op": "repeat", "child": 1, "count": cb},
    ]


@pytest.mark.parametrize("la,lb", [(3, 5), (4, 6), (2, 3), (3, 4), (5, 7)])
def test_repeats_with_matching_prefix_distinct_units(la, lb):
    # 单元：B 是 A 的前缀再补若干音，使开头若干音相同
    base = [60 + i for i in range(max(la, lb))]
    ua = base[:la]
    ub = base[:lb]
    nodes = repeat_pair_nodes(ua, ub, 333, 277)
    assert_naive(nodes, 2, 3)


def test_periods_3_and_5_diff_within_second_joint_window():
    """构造两侧在一个联合窗口后发散：用 concat 改变第二个窗口内容。"""
    # A = [60,61,62] 重复；B = concat([60,61,62,60,61] 重复 2 次, [70...])
    nodes = [
        {"id": 0, "op": "phrase",
         "items": [{"type": "note", "pitch": p, "duration": {"n": 1}} for p in [60, 61, 62]]},
        {"id": 1, "op": "phrase",
         "items": [{"type": "note", "pitch": p, "duration": {"n": 1}}
                   for p in [60, 61, 62, 60, 61]]},
        {"id": 2, "op": "repeat", "child": 0, "count": 400},
        {"id": 3, "op": "repeat", "child": 1, "count": 240},
    ]
    assert_naive(nodes, 2, 3)


@pytest.mark.parametrize("seed", range(30))
def test_random_distinct_repeated_units(seed):
    rng = random.Random(seed)
    for _ in range(8):
        la, lb = rng.randint(1, 6), rng.randint(1, 6)
        ua = [rng.randint(40, 80) for _ in range(la)]
        ub = [rng.randint(40, 80) for _ in range(lb)]
        ca, cb = rng.randint(1, 400), rng.randint(1, 400)
        nodes = repeat_pair_nodes(ua, ub, ca, cb)
        assert_naive(nodes, 2, 3)


@pytest.mark.parametrize("seed", range(20))
def test_random_repeats_with_wrappers(seed):
    rng = random.Random(5000 + seed)
    nid = 0

    def phrase(pitches, rests=False):
        nonlocal nid
        items = []
        for i, p in enumerate(pitches):
            items.append({"type": "note", "pitch": p, "duration": {"n": rng.randint(1, 2)}})
            if rests and i % 2 == 0 and rng.random() < 0.3:
                items.append({"type": "rest", "duration": {"n": 1}})
        node = {"id": nid, "op": "phrase", "items": items}
        cur = nid
        nid += 1
        return cur, node

    nodes = []
    roots = []
    for _ in range(2):
        pitches = [rng.randint(30, 90) for _ in range(rng.randint(1, 5))]
        cur, node = phrase(pitches, rests=True)
        nodes.append(node)
        rep = {"id": nid, "op": "repeat", "child": cur, "count": rng.randint(1, 200)}
        cur = nid
        nid += 1
        nodes.append(rep)
        for _ in range(rng.randint(0, 2)):
            choice = rng.choice(["transpose", "reverse", "stretch"])
            if choice == "transpose":
                n = {"id": nid, "op": "transpose", "child": cur, "k": rng.choice([-3, 0, 5])}
            elif choice == "stretch":
                n = {"id": nid, "op": "stretch", "child": cur,
                     "q": {"n": rng.randint(1, 2), "d": rng.randint(1, 2)}}
            else:
                n = {"id": nid, "op": "reverse", "child": cur}
            cur = nid
            nid += 1
            nodes.append(n)
        roots.append(cur)
    assert_naive(nodes, roots[0], roots[1])


def test_distinct_repeat_units_different_periods_big_counts():
    """不同长度重复单元 + 大计数 + 开头若干音相同：必须报真实第一差异（beat 6），
    且不能因为签名周期跳转而越过它。"""
    def N(p):
        return {"type": "note", "pitch": p, "duration": {"n": 1}}

    nodes = [
        {"id": 0, "op": "phrase", "items": [N(60), N(62), N(64), N(65)]},
        {"id": 1, "op": "phrase",
         "items": [N(60), N(62), N(64), N(65), N(60), N(62)]},
        {"id": 2, "op": "repeat", "child": 0, "count": 100_000_000},
        {"id": 3, "op": "repeat", "child": 1, "count": 100_000_000},
    ]
    import time

    a, b = compile_pair(nodes, 2, 3)
    t0 = time.time()
    d = compare_terms(a, b)
    assert time.time() - t0 < 5
    assert d is not None
    assert d.beat == 6
    assert d.expected.event.pitch == 64
    assert d.handwritten.event.pitch == 60


def test_identical_content_distinct_repeat_nodes_big_counts_equal():
    """重复单元内容相同但节点不同（非结构共享），大计数也应判等且快速。"""
    def N(p):
        return {"type": "note", "pitch": p, "duration": {"n": 1}}

    nodes = [
        {"id": 0, "op": "phrase", "items": [N(60), N(62), N(64)]},
        {"id": 1, "op": "phrase", "items": [N(60), N(62), N(64)]},
        {"id": 2, "op": "repeat", "child": 0, "count": 100_000_000},
        {"id": 3, "op": "repeat", "child": 1, "count": 100_000_000},
    ]
    a, b = compile_pair(nodes, 2, 3)
    assert compare_terms(a, b) is None


def test_distinct_repeat_units_early_end_different_lengths():
    """不同重复次数，单侧先结束：结束拍点必须是较短一侧的真实末尾。"""
    def N(p):
        return {"type": "note", "pitch": p, "duration": {"n": 1}}

    nodes = [
        {"id": 0, "op": "phrase", "items": [N(60)]},
        {"id": 1, "op": "phrase", "items": [N(60)]},
        {"id": 2, "op": "repeat", "child": 0, "count": 6},
        {"id": 3, "op": "repeat", "child": 1, "count": 4},
    ]
    assert_naive(nodes, 2, 3)


@pytest.mark.parametrize("la,lb,ca,cb", [
    (3, 5, 333, 277), (4, 6, 1000, 999), (2, 3, 5000, 4000), (5, 2, 123, 456),
])
def test_mismatched_repeats_against_naive_param(la, lb, ca, cb):
    base = [60 + i for i in range(max(la, lb))]
    nodes = repeat_pair_nodes(base[:la], base[:lb], ca, cb)
    assert_naive(nodes, 2, 3)
