"""大规模对抗：在重复结构任意位置制造单处变异，压缩比对必须精确定位。

朴素对照物化到“变异点之后”即停（不物化巨型序列），压缩侧 repeat 计数
取到数亿；两侧事件数相同时直接对照拍点与事件。
"""

from __future__ import annotations

import random

import pytest

from app.core.compare import compare_terms
from app.core.compiler import Compiler
from app.models import AnalyzeRequest


def note(p, n=1, d=1):
    return {"type": "note", "pitch": p, "duration": {"n": n, "d": d}, "tie_next": False}


def rest(n=1, d=1):
    return {"type": "rest", "duration": {"n": n, "d": d}}


def _compile(nodes, ea, hb):
    req = AnalyzeRequest.model_validate(
        {"expected_root": ea, "handwritten_root": hb, "nodes": nodes}
    )
    c = Compiler(req.nodes)
    c.compile_all()
    return c.term_for(ea, ""), c.term_for(hb, "")


@pytest.mark.parametrize("seed", range(12))
def test_mutation_at_random_position_plain_repeat(seed):
    rng = random.Random(seed)
    # 基础单元 2~6 个音符（无首尾休止，避免合并模式带来的计数差异）
    unit_len = rng.randint(2, 6)
    pitches = [rng.randint(30, 90) for _ in range(unit_len)]
    unit = [note(p) for p in pitches]
    C = rng.choice([100_000, 10_000_000, 300_000_000])
    mut = rng.randrange(C * unit_len)  # 变异的全局事件位置

    nodes = [
        {"id": 0, "op": "phrase", "items": unit},
        {"id": 1, "op": "repeat", "child": 0, "count": C},
    ]
    # 手写：把第 mut 个事件的音高 +1（仍在 MIDI 范围内）
    gi, li = divmod(mut, unit_len)
    new_pitches = pitches.copy()
    new_pitches[li] += 1
    alt_unit = [note(p) for p in new_pitches]
    nodes += [
        {"id": 2, "op": "phrase", "items": alt_unit},
        {"id": 3, "op": "repeat", "child": 0, "count": gi},
        {"id": 4, "op": "repeat", "child": 2, "count": 1},
        {"id": 5, "op": "repeat", "child": 0, "count": C - gi - 1},
        {"id": 6, "op": "concat", "children": [3, 4, 5]},
    ]
    a, b = _compile(nodes, 1, 6)
    assert a.count == b.count
    d = compare_terms(a, b)
    assert d is not None
    assert d.beat == mut  # 单位时长皆为 1
    assert d.expected.event.pitch == pitches[li]
    assert d.handwritten.event.pitch == pitches[li] + 1


@pytest.mark.parametrize("seed", range(8))
def test_mutation_inside_merged_rest_repeat(seed):
    """合并 repeat：用朴素小计数对照，定位副本内部音符变异。

    手写 = 完整重复 (target_copy 次) + 变异副本 1 次 + 剩余重复；
    朴素侧同样物化（事件数小）。concat 边界休止合并由参考实现统一处理。
    """
    rng = random.Random(700 + seed)
    inner = [note(p) for p in [rng.randint(30, 80) for _ in range(rng.randint(1, 3))]]
    unit = [rest(1)] + inner + [rest(2)]
    C = rng.randint(3, 40)
    target_copy = rng.randrange(C)
    target_inner = rng.randrange(len(inner))
    unit_idx = target_inner + 1

    alt = []
    for i, x in enumerate(unit):
        alt.append(note(x["pitch"] + 3) if i == unit_idx else dict(x))

    from .naive import first_diff_naive, naive_events

    nodes = [
        {"id": 0, "op": "phrase", "items": unit},
        {"id": 1, "op": "repeat", "child": 0, "count": C},
        {"id": 2, "op": "phrase", "items": alt},
        {"id": 3, "op": "repeat", "child": 0, "count": target_copy},
        {"id": 4, "op": "repeat", "child": 2, "count": 1},
        {"id": 5, "op": "repeat", "child": 0, "count": C - target_copy - 1},
        {"id": 6, "op": "concat", "children": [3, 4, 5]},
    ]
    a, b = _compile(nodes, 1, 6)
    d = compare_terms(a, b)
    ea = naive_events(nodes, 1)
    hb = naive_events(nodes, 6)
    nd = first_diff_naive(ea, hb)
    assert d is not None and nd is not None
    beat, idx = nd
    assert d.beat == beat
    assert (d.expected.event.pitch, d.expected.event.duration) == ea[idx]
    assert (d.handwritten.event.pitch, d.handwritten.event.duration) == hb[idx]
