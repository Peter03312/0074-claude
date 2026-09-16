"""大块跳转专项：用朴素实现对照压缩比对器在大重复结构下的第一差异定位。

朴素侧事件数控制在百万级以内以便物化；压缩侧的结构步数与事件数无关。
"""

from __future__ import annotations


import pytest

from app.core.compare import compare_terms
from app.core.compiler import Compiler
from app.models import AnalyzeRequest

from .naive import first_diff_naive, naive_events


def f(n, d=1):
    return {"n": n, "d": d}


def note(p, n=1, d=1):
    return {"type": "note", "pitch": p, "duration": f(n, d), "tie_next": False}


def rest(n=1, d=1):
    return {"type": "rest", "duration": f(n, d)}


def compile_pair(nodes, ea, hb):
    req = AnalyzeRequest.model_validate(
        {"expected_root": ea, "handwritten_root": hb, "nodes": nodes}
    )
    c = Compiler(req.nodes)
    c.compile_all()
    return c.term_for(ea, ""), c.term_for(hb, "")


def assert_diff_matches(nodes, ea, hb):
    a, b = compile_pair(nodes, ea, hb)
    ea_seq = naive_events(nodes, ea)
    hb_seq = naive_events(nodes, hb)
    nd = first_diff_naive(ea_seq, hb_seq)
    d = compare_terms(a, b)
    if nd is None:
        assert d is None
        return
    beat, idx = nd
    assert d is not None
    assert d.beat == beat
    if idx < len(ea_seq):
        assert (d.expected.event.pitch, d.expected.event.duration) == ea_seq[idx]
    else:
        assert d.expected is None
    if idx < len(hb_seq):
        assert (d.handwritten.event.pitch, d.handwritten.event.duration) == hb_seq[idx]
    else:
        assert d.handwritten is None


@pytest.mark.parametrize("c", [1, 2, 7, 100, 1000, 50_000])
def test_plain_repeat_equal_and_naive(c):
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(62), note(64)]},
        {"id": 1, "op": "repeat", "child": 0, "count": c},
        {"id": 2, "op": "repeat", "child": 1, "count": c if c <= 1000 else 3},
    ]
    a, _ = compile_pair(nodes, 2, 2)
    assert compare_terms(a, a) is None


@pytest.mark.parametrize("c", [3, 17, 1000, 40_000])
def test_plain_repeat_last_pitch_changed(c):
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(62)]},
        {"id": 1, "op": "repeat", "child": 0, "count": c},
        {"id": 10, "op": "repeat", "child": 0, "count": c - 1},
        {"id": 11, "op": "phrase", "items": [note(60), note(90)]},
        {"id": 12, "op": "concat", "children": [10, 11]},
    ]
    assert_diff_matches(nodes, 1, 12)


@pytest.mark.parametrize("c", [2, 5, 100, 5000])
def test_merged_rest_repeat_equal_and_naive(c):
    nodes = [
        {"id": 0, "op": "phrase", "items": [rest(1), note(60), note(62), rest(2)]},
        {"id": 1, "op": "repeat", "child": 0, "count": c},
    ]
    assert_diff_matches(nodes, 1, 1)


@pytest.mark.parametrize("c", [2, 3, 10, 1000])
def test_merged_rest_repeat_with_changed_inner(c):
    # 手写把其中一个内部音符改错（构造在第一个副本末尾）
    nodes = [
        {"id": 0, "op": "phrase", "items": [rest(1), note(60), note(62), rest(2)]},
        {"id": 1, "op": "repeat", "child": 0, "count": c},
        {"id": 2, "op": "phrase", "items": [rest(1), note(60), note(77), rest(2)]},
        {"id": 3, "op": "repeat", "child": 0, "count": c - 1},
        {"id": 4, "op": "concat", "children": [3, 2]},
    ]
    # 两侧都是首尾休止部分，concat 边界会再合并 → 与朴素对齐
    assert_diff_matches(nodes, 1, 4)


def test_stretch_over_huge_repeat():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60, 1, 2), note(62)]},
        {"id": 1, "op": "repeat", "child": 0, "count": 100_000},
        {"id": 2, "op": "stretch", "child": 1, "q": f(3, 2)},
        {"id": 3, "op": "stretch", "child": 1, "q": f(3, 2)},
    ]
    a, b = compile_pair(nodes, 2, 3)
    assert compare_terms(a, b) is None


def test_nested_repeat_late_rest_diff():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), rest(1)]},
        {"id": 1, "op": "phrase", "items": [note(60), rest(2)]},
        {"id": 2, "op": "repeat", "child": 0, "count": 1000},
        {"id": 3, "op": "repeat", "child": 2, "count": 1000},
        {"id": 4, "op": "repeat", "child": 1, "count": 1000},
        {"id": 5, "op": "repeat", "child": 4, "count": 1000},
    ]
    assert_diff_matches(nodes, 3, 5)


def test_uneven_periods_lcm_skip():
    # 两侧副本长度不同（3 与 5），联合周期 lcm=15
    nodes = [
        {"id": 0, "op": "phrase",
         "items": [note(60), note(62), note(64), note(65), note(67)]},
        {"id": 1, "op": "phrase", "items": [note(60), note(62), note(64)]},
        # 周期 5 的序列重复
        {"id": 10, "op": "repeat", "child": 0, "count": 3000},
        # 周期 5 vs 周期 3 但前 5 个音相同；第三音之后会不同
        {"id": 11, "op": "repeat", "child": 1, "count": 5000},
    ]
    a, b = compile_pair(nodes, 10, 11)
    d = compare_terms(a, b)
    # 朴素对照（事件数 15000）
    ea = naive_events(nodes, 10)
    hb = naive_events(nodes, 11)
    nd = first_diff_naive(ea, hb)
    assert d is not None and nd is not None
    assert d.beat == nd[0]


def test_different_repeated_units_matching_prefix_does_not_skip_diff():
    """两侧重复单元不同（非共享孩子）：联合周期内差异必须被签名兜底捕获。

    单元 A 周期 3：[60,60,62]；单元 B 周期 3：[60,60,63]。
    朴素上前两个事件相同，第三个不同；即使重复数巨大也必须在拍点 2 报差异。
    （本用例不做朴素物化，只验证大计数下不越过早期差异。）
    """
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(60), note(62)]},
        {"id": 1, "op": "phrase", "items": [note(60), note(60), note(63)]},
        {"id": 10, "op": "repeat", "child": 0, "count": 100_000},
        {"id": 11, "op": "repeat", "child": 1, "count": 100_000},
    ]
    a, b = compile_pair(nodes, 10, 11)
    d = compare_terms(a, b)
    assert d is not None
    assert d.beat == 2
    assert d.expected.event.pitch == 62
    assert d.handwritten.event.pitch == 63


def test_different_units_late_within_joint_cycle():
    """周期 2 与周期 3 的两条序列，差异落在联合周期内，朴素对照（小计数）。"""
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(62)]},
        {"id": 1, "op": "phrase", "items": [note(60), note(62), note(64)]},
        {"id": 10, "op": "repeat", "child": 0, "count": 1000},
        {"id": 11, "op": "repeat", "child": 1, "count": 1000},
    ]
    a, b = compile_pair(nodes, 10, 11)
    ea = naive_events(nodes, 10)
    hb = naive_events(nodes, 11)
    nd = first_diff_naive(ea, hb)
    d = compare_terms(a, b)
    assert d is not None and nd is not None
    assert d.beat == nd[0] == 2
    assert d.expected.event.pitch == 60
    assert d.handwritten.event.pitch == 64


def test_different_units_huge_counts_report_early_diff_fast():
    """不同重复单元、大计数：早期差异必须在任何跳过发生前秒级报出。"""
    import time

    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(60), note(62)]},
        {"id": 1, "op": "phrase", "items": [note(60), note(60), note(63)]},
        {"id": 10, "op": "repeat", "child": 0, "count": 1_000_000_000},
        {"id": 11, "op": "repeat", "child": 1, "count": 1_000_000_000},
    ]
    a, b = compile_pair(nodes, 10, 11)
    t0 = time.time()
    d = compare_terms(a, b)
    assert time.time() - t0 < 5
    assert d is not None and d.beat == 2
    assert d.expected.event.pitch == 62 and d.handwritten.event.pitch == 63


def test_huge_merged_rest_repeat_late_inner_mutation():
    """2 亿次首尾休止重复，最后一副本内部音被改：必须秒级定位（不得逐事件走）。"""
    C = 200_000_000
    nodes = [
        {"id": 0, "op": "phrase",
         "items": [rest(1), note(60), note(62), rest(2)]},
        {"id": 1, "op": "repeat", "child": 0, "count": C},
        {"id": 2, "op": "phrase",
         "items": [rest(1), note(60), note(99), rest(2)]},
        {"id": 3, "op": "repeat", "child": 0, "count": C - 1},
        {"id": 4, "op": "concat", "children": [3, 2]},
    ]
    import time

    a, b = compile_pair(nodes, 1, 4)
    t0 = time.time()
    d = compare_terms(a, b)
    assert time.time() - t0 < 5
    # concat[repeat(单元 4 拍, C-1 次), 变异单元]，合并边界休止；
    # 变异副本第二个内部音出现在 (C-1)*4 + 1
    assert d.beat == 999_999_997
    assert d.expected.event.pitch == 62
    assert d.handwritten.event.pitch == 99
