"""固定场景测试：题面语义、来源链、422 校验与端点/规模。"""

from __future__ import annotations

from fractions import Fraction

from fastapi.testclient import TestClient

from app.core.compare import compare_terms
from app.core.compiler import Compiler
from app.core.cursor import Cursor
from app.main import app
from app.models import AnalyzeRequest


client = TestClient(app)


def f(n, d=1):
    return {"n": n, "d": d}


def note(p, n=1, d=1, tie=False):
    return {"type": "note", "pitch": p, "duration": f(n, d), "tie_next": tie}


def rest(n=1, d=1):
    return {"type": "rest", "duration": f(n, d)}


def compile_terms(nodes, *roots):
    req = AnalyzeRequest.model_validate(
        {"expected_root": roots[0], "handwritten_root": roots[-1], "nodes": nodes}
    )
    c = Compiler(req.nodes)
    c.compile_all()
    return c, [c.term_for(r, "") for r in roots]


def stream(term):
    cur = Cursor(term)
    out = []
    while not cur.exhausted:
        s = cur.peek()
        out.append((s.event.pitch, s.event.duration, cur.beat(), s.chain))
        cur.advance()
    return out


# ---------- 规范化 ----------
def test_tie_merges_adjacent_unison():
    nodes = [
        {
            "id": 0,
            "op": "phrase",
            "items": [note(60, 1, 2, True), note(60, 1, 2), note(64, 1)],
        }
    ]
    (t,) = compile_terms(nodes, 0)[1]
    s = stream(t)
    assert [(p, d) for p, d, _, _ in s] == [(60, Fraction(1)), (64, Fraction(1))]
    # 合并事件来源取最早贡献项（item 0）
    assert s[0][3][0].item_index == 0


def test_unison_without_tie_keeps_two_attacks():
    nodes = [{"id": 0, "op": "phrase", "items": [note(60), note(60)]}]
    (t,) = compile_terms(nodes, 0)[1]
    assert [(p, d) for p, d, _, _ in stream(t)] == [(60, 1), (60, 1)]


def test_illegal_tie_to_different_pitch_is_422():
    payload = {
        "expected_root": 0,
        "handwritten_root": 0,
        "nodes": [{"id": 0, "op": "phrase",
                   "items": [note(60, tie=True), note(62)]}],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422
    locs = [e["loc"] for e in r.json()["detail"]]
    assert "/nodes/0/items/1" in locs


def test_illegal_tie_to_rest_is_422():
    payload = {
        "expected_root": 0,
        "handwritten_root": 0,
        "nodes": [{"id": 0, "op": "phrase", "items": [note(60, tie=True), rest()]}],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


def test_adjacent_rests_merge_within_phrase():
    nodes = [{"id": 0, "op": "phrase", "items": [rest(1, 2), rest(1, 2), note(60)]}]
    (t,) = compile_terms(nodes, 0)[1]
    assert [(p, d) for p, d, _, _ in stream(t)] == [(None, 1), (60, 1)]


def test_cross_concat_rest_merge():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), rest(1)]},
        {"id": 1, "op": "phrase", "items": [rest(2), note(64)]},
        {"id": 2, "op": "concat", "children": [0, 1]},
    ]
    (t,) = compile_terms(nodes, 2)[1]
    s = stream(t)
    assert [(p, d) for p, d, _, _ in s] == [(60, 1), (None, 3), (64, 1)]
    # 合并休止来源取演奏顺序最早（节点 0 的尾部休止）
    chain = s[1][3]
    assert chain[0].node_id == 2  # concat
    assert chain[1].node_id == 0


def test_cross_concat_merge_with_single_rest_part():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), rest(1)]},
        {"id": 1, "op": "phrase", "items": [rest(1)]},
        {"id": 2, "op": "phrase", "items": [rest(1), note(64)]},
        {"id": 3, "op": "concat", "children": [0, 1, 2]},
    ]
    (t,) = compile_terms(nodes, 3)[1]
    assert [(p, d) for p, d, _, _ in stream(t)] == [(60, 1), (None, 3), (64, 1)]


# ---------- 变换抵消 ----------
def test_transpose_cancels():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(72)]},
        {"id": 1, "op": "transpose", "child": 0, "k": 12},
        {"id": 2, "op": "transpose", "child": 1, "k": -12},
    ]
    _, (a, b) = compile_terms(nodes, 0, 2)
    assert compare_terms(a, b) is None


def test_mirror_self_inverse_half_axis():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(63), note(50)]},
        {"id": 1, "op": "pitch_mirror", "child": 0, "axis": f(60)},
        {"id": 2, "op": "pitch_mirror", "child": 1, "axis": f(60)},
    ]
    _, (a, b) = compile_terms(nodes, 0, 2)
    assert compare_terms(a, b) is None


def test_stretch_cancels():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60, 1, 2), rest(1)]},
        {"id": 1, "op": "stretch", "child": 0, "q": f(3, 2)},
        {"id": 2, "op": "stretch", "child": 1, "q": f(2, 3)},
    ]
    _, (a, b) = compile_terms(nodes, 0, 2)
    assert compare_terms(a, b) is None


def test_reverse_self_inverse():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), rest(1), note(72)]},
        {"id": 1, "op": "reverse", "child": 0},
        {"id": 2, "op": "reverse", "child": 1},
    ]
    _, (a, b) = compile_terms(nodes, 0, 2)
    assert compare_terms(a, b) is None


def test_mirror_values():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(64)]},
        {"id": 1, "op": "pitch_mirror", "child": 0, "axis": f(62)},
    ]
    (t,) = compile_terms(nodes, 1)[1]
    assert [(p, d) for p, d, _, _ in stream(t)] == [(64, 1), (60, 1)]


def test_mirror_half_integer_axis():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(61)]},
        {"id": 1, "op": "pitch_mirror", "child": 0, "axis": f(121, 2)},
    ]
    (t,) = compile_terms(nodes, 1)[1]
    assert [(p, d) for p, d, _, _ in stream(t)] == [(61, 1), (60, 1)]


def test_bad_mirror_axis_denominator_422():
    payload = {
        "expected_root": 1,
        "handwritten_root": 1,
        "nodes": [
            {"id": 0, "op": "phrase", "items": [note(60)]},
            {"id": 1, "op": "pitch_mirror", "child": 0, "axis": f(121, 4)},
        ],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422
    assert "/nodes/1/axis" in r.json()["detail"][0]["loc"]


# ---------- 越界 ----------
def test_transpose_out_of_range_422():
    payload = {
        "expected_root": 1,
        "handwritten_root": 1,
        "nodes": [
            {"id": 0, "op": "phrase", "items": [note(120)]},
            {"id": 1, "op": "transpose", "child": 0, "k": 10},
        ],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


def test_mirror_out_of_range_422():
    payload = {
        "expected_root": 1,
        "handwritten_root": 1,
        "nodes": [
            {"id": 0, "op": "phrase", "items": [note(10)]},
            {"id": 1, "op": "pitch_mirror", "child": 0, "axis": f(0)},
        ],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


# ---------- repeat ----------
def test_repeat_zero_is_empty():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60)]},
        {"id": 1, "op": "repeat", "child": 0, "count": 0},
    ]
    (t,) = compile_terms(nodes, 1)[1]
    assert t.count == 0
    assert t.duration == 0


def test_repeat_count_bounds_422():
    payload = {
        "expected_root": 1,
        "handwritten_root": 1,
        "nodes": [
            {"id": 0, "op": "phrase", "items": [note(60)]},
            {"id": 1, "op": "repeat", "child": 0, "count": 1_000_000_001},
        ],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


def test_repeat_merge_boundary_rests():
    nodes = [
        {"id": 0, "op": "phrase", "items": [rest(1), note(60), rest(2)]},
        {"id": 1, "op": "repeat", "child": 0, "count": 3},
    ]
    (t,) = compile_terms(nodes, 1)[1]
    s = [(p, d) for p, d, _, _ in stream(t)]
    # F, n, R(3), n, R(3), n, T(2)
    assert s == [
        (None, 1), (60, 1), (None, 3),
        (60, 1), (None, 3), (60, 1), (None, 2),
    ]
    assert t.duration == 3 * 4


def test_repeat_source_iteration_numbers():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60)]},
        {"id": 1, "op": "repeat", "child": 0, "count": 3},
    ]
    (t,) = compile_terms(nodes, 1)[1]
    iters = [chain[0].iteration for _, _, _, chain in stream(t)]
    assert iters == [0, 1, 2]


def test_merged_boundary_rest_source_is_earliest_contributor():
    """合并休止由“上一副本尾部 + 下一副本头部”贡献，取演奏顺序较早者。"""
    nodes = [
        {"id": 0, "op": "phrase", "items": [rest(1), note(60), rest(2)]},
        {"id": 1, "op": "repeat", "child": 0, "count": 3},
    ]
    (t,) = compile_terms(nodes, 1)[1]
    s = stream(t)
    # 两个边界合并休止分别在 beat 2、6；来源迭代号取较早副本 0、1
    boundary = [row for row in s if row[0] is None and row[1] == 3]
    assert [b for _, _, b, _ in boundary] == [2, 6]
    assert [ch[0].iteration for _, _, _, ch in boundary] == [0, 1]
    assert boundary[0][3][0].item_index is None
    assert boundary[0][3][1].item_index == 2  # 尾部休止（较早贡献项）


def test_concat_merged_rest_source_prefers_earlier_node_id():
    """跨 concat 合并休止的来源取演奏顺序最早（节点编号更小）的贡献项。"""
    nodes = [
        {"id": 5, "op": "phrase", "items": [note(60), rest(1)]},
        {"id": 9, "op": "phrase", "items": [rest(2), note(64)]},
        {"id": 2, "op": "concat", "children": [5, 9]},
    ]
    (t,) = compile_terms(nodes, 2)[1]
    s = stream(t)
    merged = [row for row in s if row[0] is None][0]
    # 链：concat(2) → 较早贡献部分 5 的尾休止 item 1
    node_ids = [c.node_id for c in merged[3]]
    assert node_ids[0] == 2
    assert node_ids[1] == 5
    assert merged[3][1].item_index == 1


# ---------- 第一差异端点 ----------
def test_difference_mid_pitch_and_beat():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60, 1, 2), note(62), note(64)]},
        {"id": 1, "op": "phrase", "items": [note(60, 1, 2), note(63), note(64)]},
    ]
    _, (a, b) = compile_terms(nodes, 0, 1)
    d = compare_terms(a, b)
    assert d.beat == Fraction(1, 2)
    assert d.expected.event.pitch == 62
    assert d.handwritten.event.pitch == 63


def test_difference_duration_gives_beat_rest_after():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60, 1), note(64)]},
        {"id": 1, "op": "phrase", "items": [note(60, 2), note(64)]},
    ]
    _, (a, b) = compile_terms(nodes, 0, 1)
    d = compare_terms(a, b)
    # 第一个音的时值就不同，最早差异拍点为 0
    assert d.beat == 0
    assert d.expected.event.pitch == 60
    assert d.handwritten.event.pitch == 60
    assert d.expected.event.duration == 1
    assert d.handwritten.event.duration == 2


def test_handwritten_ends_early():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(62), note(64)]},
        {"id": 1, "op": "phrase", "items": [note(60), note(62)]},
    ]
    _, (a, b) = compile_terms(nodes, 0, 1)
    d = compare_terms(a, b)
    assert d.beat == 2
    assert d.expected.event.pitch == 64
    assert d.handwritten is None


def test_expected_ends_early():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60)]},
        {"id": 1, "op": "phrase", "items": [note(60), note(62)]},
    ]
    _, (a, b) = compile_terms(nodes, 0, 1)
    d = compare_terms(a, b)
    assert d.beat == 1
    assert d.expected is None
    assert d.handwritten.event.pitch == 62


# ---------- 万亿级 ----------
def test_trillion_equal_compressed_only():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(62)]},
        {"id": 1, "op": "repeat", "child": 0, "count": 1_000_000},
        {"id": 2, "op": "repeat", "child": 1, "count": 1_000_000},
    ]
    (t,) = compile_terms(nodes, 2)[1]
    assert t.count == 2_000_000_000_000
    assert compare_terms(t, t) is None


def test_trillion_late_difference_is_located():
    # 预期：repeat(repeat([60,62]))；手写只把“整曲最后一个音”改为 63。
    # 手写结构：外层 999_999 次完整内层副本 + 一个“几乎完整的内层副本”，
    # 该内层副本 = 999_999 次 [60,62] + [60,63]。
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(62)]},
        {"id": 1, "op": "repeat", "child": 0, "count": 1_000_000},
        {"id": 2, "op": "repeat", "child": 1, "count": 1_000_000},
        {"id": 10, "op": "repeat", "child": 1, "count": 999_999},
        {"id": 20, "op": "repeat", "child": 0, "count": 999_999},
        {"id": 21, "op": "phrase", "items": [note(60), note(63)]},
        {"id": 22, "op": "concat", "children": [20, 21]},
        {"id": 23, "op": "concat", "children": [10, 22]},
    ]
    c, terms = compile_terms(nodes, 2, 23)
    a, b = terms
    assert a.count == b.count == 2_000_000_000_000
    d = compare_terms(a, b)
    assert d is not None
    assert d.beat == 2_000_000_000_000 - 1
    assert d.expected.event.pitch == 62
    assert d.handwritten.event.pitch == 63


def test_trillion_late_difference_through_concat_structure():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(62)]},
        {"id": 1, "op": "repeat", "child": 0, "count": 1_000_000},
        {"id": 2, "op": "repeat", "child": 1, "count": 1_000_000},
        # 手写：少一个外层副本，再补一个“60,63”结尾
        {"id": 10, "op": "repeat", "child": 1, "count": 999_999},
        {"id": 11, "op": "phrase", "items": [note(60)]},
        {"id": 12, "op": "phrase", "items": [note(63)]},
        {"id": 13, "op": "concat", "children": [11, 12]},
        {"id": 14, "op": "concat", "children": [10, 13]},
    ]
    c, terms = compile_terms(nodes, 2, 14)
    a, b = terms
    d = compare_terms(a, b)
    # 手写在补尾处少了一个完整内层副本：差异首先落在补入的 63 音上
    assert d is not None
    assert d.beat == 1_999_998_000_001
    assert d.expected.event.pitch == 62
    assert d.handwritten.event.pitch == 63


def test_trillion_early_termination():
    nodes = [
        {"id": 0, "op": "phrase", "items": [note(60), note(62)]},
        {"id": 1, "op": "repeat", "child": 0, "count": 1_000_000},
        {"id": 2, "op": "repeat", "child": 1, "count": 1_000_000},
        {"id": 3, "op": "repeat", "child": 1, "count": 999_999},
    ]
    _, (a, b) = compile_terms(nodes, 2, 3)
    d = compare_terms(a, b)
    assert d.beat == b.count
    assert d.handwritten is None
    assert d.expected.event.pitch == 60


# ---------- DAG 校验 ----------
def test_unknown_child_422():
    payload = {
        "expected_root": 1,
        "handwritten_root": 1,
        "nodes": [{"id": 1, "op": "reverse", "child": 99}],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


def test_cycle_is_422():
    payload = {
        "expected_root": 1,
        "handwritten_root": 1,
        "nodes": [
            {"id": 1, "op": "reverse", "child": 2},
            {"id": 2, "op": "reverse", "child": 1},
        ],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


def test_self_cycle_422():
    payload = {
        "expected_root": 1,
        "handwritten_root": 1,
        "nodes": [{"id": 1, "op": "reverse", "child": 1}],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


def test_duplicate_node_id_422():
    payload = {
        "expected_root": 0,
        "handwritten_root": 0,
        "nodes": [
            {"id": 0, "op": "phrase", "items": []},
            {"id": 0, "op": "phrase", "items": [note(60)]},
        ],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


def test_unknown_op_422():
    payload = {
        "expected_root": 0,
        "handwritten_root": 0,
        "nodes": [{"id": 0, "op": "frobnicate", "items": []}],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


def test_unknown_root_422():
    payload = {
        "expected_root": 7,
        "handwritten_root": 0,
        "nodes": [{"id": 0, "op": "phrase", "items": [note(60)]}],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == "/expected_root"


def test_bad_fraction_422():
    payload = {
        "expected_root": 0,
        "handwritten_root": 0,
        "nodes": [{"id": 0, "op": "phrase", "items": [note(60, 1, 0)]}],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422
    assert r.json()["detail"][0]["loc"] == "/nodes/0/items/0/duration/d"


def test_nested_rest_fraction_path_has_no_discriminator_tags():
    payload = {
        "expected_root": 0,
        "handwritten_root": 0,
        "nodes": [
            {"id": 0, "op": "phrase", "items": [rest(1, 0)]},
        ],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422
    loc = r.json()["detail"][0]["loc"]
    assert loc == "/nodes/0/items/0/duration/d"
    for tag in ("phrase", "rest", "note"):
        assert f"/{tag}/" not in loc


def test_non_positive_duration_semantic_422():
    payload = {
        "expected_root": 0,
        "handwritten_root": 0,
        "nodes": [{"id": 0, "op": "phrase", "items": [{"type": "note", "pitch": 60,
                "duration": {"n": 0, "d": 1}}]}],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


def test_pitch_out_of_midi_422():
    payload = {
        "expected_root": 0,
        "handwritten_root": 0,
        "nodes": [{"id": 0, "op": "phrase", "items": [note(128)]}],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


def test_expanded_size_limit_422():
    # 两个嵌套 repeat 让规范事件数超过 10^15
    payload = {
        "expected_root": 2,
        "handwritten_root": 2,
        "nodes": [
            {"id": 0, "op": "phrase", "items": [note(60), note(62)]},
            {"id": 1, "op": "repeat", "child": 0, "count": 1_000_000_000},
            {"id": 2, "op": "repeat", "child": 1, "count": 1_000_000},
        ],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


def test_too_many_nodes_422():
    nodes = [{"id": i, "op": "phrase", "items": []} for i in range(2001)]
    payload = {"expected_root": 0, "handwritten_root": 0, "nodes": nodes}
    r = client.post("/analyze", json=payload)
    assert r.status_code == 422


# ---------- HTTP 响应 ----------
def test_api_equal_response():
    payload = {
        "expected_root": 0,
        "handwritten_root": 1,
        "nodes": [
            {"id": 0, "op": "phrase", "items": [note(60, 1, 2), rest(1)]},
            {"id": 1, "op": "phrase", "items": [note(60, 1, 2), rest(2, 2)]},
        ],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["equal"] is True
    assert body["first_difference"] is None
    assert body["event_count"] == 2
    assert body["total_duration"] == {"n": 3, "d": 2}


def test_api_difference_payload_and_chain():
    payload = {
        "expected_root": 2,
        "handwritten_root": 3,
        "nodes": [
            {"id": 0, "op": "phrase", "items": [note(60), note(72)]},
            {"id": 1, "op": "phrase", "items": [note(60), note(71)]},
            {"id": 2, "op": "transpose", "child": 0, "k": 0},
            {"id": 3, "op": "transpose", "child": 1, "k": 0},
        ],
    }
    r = client.post("/analyze", json=payload)
    assert r.status_code == 200
    body = r.json()
    assert body["equal"] is False
    fd = body["first_difference"]
    assert fd["beat"] == {"n": 1, "d": 1}
    assert fd["expected"]["event"]["pitch"] == 72
    assert fd["handwritten"]["event"]["pitch"] == 71
    # 来源链：transpose 节点 2 → phrase 节点 0 的 item 1
    chain = fd["expected"]["source_chain"]
    assert chain == [
        {"node_id": 2, "repeat_iteration": None, "item_index": None},
        {"node_id": 0, "repeat_iteration": None, "item_index": 1},
    ]


def test_api_rest_side_view():
    payload = {
        "expected_root": 0,
        "handwritten_root": 1,
        "nodes": [
            {"id": 0, "op": "phrase", "items": [rest(1)]},
            {"id": 1, "op": "phrase", "items": [note(60)]},
        ],
    }
    r = client.post("/analyze", json=payload)
    body = r.json()
    fd = body["first_difference"]
    assert fd["expected"]["event"] == {"kind": "rest", "pitch": None,
                                       "duration": {"n": 1, "d": 1}}
    assert fd["handwritten"]["event"]["kind"] == "note"


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_no_floats_in_response():
    payload = {
        "expected_root": 0,
        "handwritten_root": 1,
        "nodes": [
            {"id": 0, "op": "phrase", "items": [note(60, 1, 3)]},
            {"id": 1, "op": "phrase", "items": [note(60, 1, 3)]},
        ],
    }
    r = client.post("/analyze", json=payload)
    text = r.text
    assert "." not in text.replace("1.0", "") or "n" in text
    data = r.json()
    assert data["total_duration"] == {"n": 1, "d": 3}
