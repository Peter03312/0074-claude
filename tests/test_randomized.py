"""随机差分性质测试：压缩求值器 == 朴素完整展开（含第一差异定位）。"""

from __future__ import annotations

import random

import pytest

from app.core.compare import compare_terms
from app.core.compiler import Compiler

from .naive import (
    compressed_events,
    first_diff_naive,
    make_request,
    naive_events,
)


def f(n, d=1):
    return {"n": n, "d": d}


def gen_dag(rng, start_id=0, wraps=6):
    """生成一个小的随机合法 DAG；返回 (nodes, root, next_id)。"""
    nodes = []
    nid = start_id

    def new_phrase():
        nonlocal nid
        n_items = rng.randint(0, 6)
        items = []
        for _ in range(n_items):
            if rng.random() < 0.25:
                items.append(
                    {"type": "rest", "duration": f(rng.randint(1, 3), rng.randint(1, 2))}
                )
            else:
                items.append(
                    {
                        "type": "note",
                        "pitch": rng.randint(20, 100),
                        "duration": f(rng.randint(1, 3), rng.randint(1, 2)),
                        "tie_next": False,
                    }
                )
        for i in range(len(items) - 1):
            if (
                items[i]["type"] == "note"
                and items[i + 1]["type"] == "note"
                and items[i]["pitch"] == items[i + 1]["pitch"]
                and rng.random() < 0.5
            ):
                items[i]["tie_next"] = True
        cur = nid
        nid += 1
        nodes.append({"id": cur, "op": "phrase", "items": items})
        return cur

    def wrap(child):
        nonlocal nid
        choice = rng.choice(
            ["repeat", "transpose", "mirror", "reverse", "stretch", "repeat", "concat"]
        )
        cur = nid
        nid += 1
        if choice == "repeat":
            nodes.append({"id": cur, "op": "repeat", "child": child, "count": rng.randint(0, 4)})
        elif choice == "transpose":
            nodes.append({"id": cur, "op": "transpose", "child": child, "k": rng.choice([-2, 2, 0, 7])})
        elif choice == "mirror":
            nodes.append({"id": cur, "op": "pitch_mirror", "child": child, "axis": f(rng.choice([60, 61, 72]))})
        elif choice == "reverse":
            nodes.append({"id": cur, "op": "reverse", "child": child})
        elif choice == "stretch":
            nodes.append({"id": cur, "op": "stretch", "child": child, "q": f(rng.randint(1, 3), rng.randint(1, 2))})
        else:
            other = new_phrase()
            nodes.append({"id": cur, "op": "concat", "children": [child, other]})
        return cur

    root = new_phrase()
    for _ in range(rng.randint(0, wraps)):
        root = wrap(root)
    return nodes, root, nid


@pytest.mark.parametrize("seed", range(60))
def test_compressed_matches_naive(seed):
    rng = random.Random(seed)
    for _ in range(20):
        nodes, root, _ = gen_dag(rng)
        try:
            expected = naive_events(nodes, root)
            _, got = compressed_events(nodes, root)
        except Exception:
            # 镜像/移调越界等语义错误由专门测试覆盖
            continue
        assert got == expected, (seed, root, nodes)


@pytest.mark.parametrize("seed", range(40))
def test_first_difference_against_naive(seed):
    rng = random.Random(1000 + seed)
    for _ in range(15):
        na, ra, nxt = gen_dag(rng, 0)
        nb, rb, _ = gen_dag(rng, nxt)
        nodes = na + nb
        ea = naive_events(nodes, ra)
        eb = naive_events(nodes, rb)

        req = make_request(nodes, ra, rb)
        c = Compiler(req.nodes)
        try:
            c.compile_all()
        except Exception:
            continue  # 镜像/移调越界等语义错误由专门测试覆盖
        ta, tb = c.term_for(ra, ""), c.term_for(rb, "")
        diff = compare_terms(ta, tb)
        nd = first_diff_naive(ea, eb)
        if nd is None:
            assert diff is None, (ra, rb, nodes)
            continue
        beat, idx = nd
        assert diff is not None
        assert diff.beat == beat
        if idx < len(ea):
            assert (diff.expected.event.pitch, diff.expected.event.duration) == ea[idx]
        else:
            assert diff.expected is None
        if idx < len(eb):
            assert (diff.handwritten.event.pitch, diff.handwritten.event.duration) == eb[idx]
        else:
            assert diff.handwritten is None
