#!/usr/bin/env bash
# verify 一次性服务：运行测试套件，随后对实际运行的分析接口发起冒烟请求，
# 成功后退出（exit 0）；任一步失败则非零退出。
set -euo pipefail

API_BASE="${API_BASE:-http://api:8000}"

echo "==> [1/3] 运行测试套件"
pytest -q

echo "==> [2/3] 等待 API 就绪：${API_BASE}/health"
python - "$API_BASE" <<'PY'
import sys
import time

import httpx

base = sys.argv[1]
deadline = time.time() + 60
last = None
while time.time() < deadline:
    try:
        r = httpx.get(f"{base}/health", timeout=2.0)
        if r.status_code == 200 and r.json().get("status") == "ok":
            print("API healthy")
            break
        last = f"status={r.status_code} body={r.text}"
    except Exception as exc:  # noqa: BLE001
        last = repr(exc)
    time.sleep(1)
else:
    print(f"API 未就绪：{last}", file=sys.stderr)
    sys.exit(1)
PY

echo "==> [3/3] 冒烟请求：相等 / 第一差异 / 万亿压缩 / 422"
python - "$API_BASE" <<'PY'
import sys

import httpx

base = sys.argv[1]


def post(payload):
    return httpx.post(f"{base}/analyze", json=payload, timeout=30.0)


def note(p, n=1, d=1, tie=False):
    return {"type": "note", "pitch": p, "duration": {"n": n, "d": d}, "tie_next": tie}


def rest(n=1, d=1):
    return {"type": "rest", "duration": {"n": n, "d": d}}


# 1) 完全相等（连音合并、休止合并）
equal_req = {
    "expected_root": 0,
    "handwritten_root": 1,
    "nodes": [
        {"id": 0, "op": "phrase",
         "items": [note(60, 1, 2, True), note(60, 1, 2), rest(1), rest(1)]},
        {"id": 1, "op": "phrase",
         "items": [note(60, 1), rest(2)]},
    ],
}
r = post(equal_req)
assert r.status_code == 200, r.text
body = r.json()
assert body["equal"] is True and body["first_difference"] is None, body

# 2) 第一差异：第二个音 62 被抄成 63，精确拍点 1
diff_req = {
    "expected_root": 0,
    "handwritten_root": 1,
    "nodes": [
        {"id": 0, "op": "phrase", "items": [note(60), note(62), note(64)]},
        {"id": 1, "op": "phrase", "items": [note(60), note(63), note(64)]},
    ],
}
r = post(diff_req)
assert r.status_code == 200, r.text
body = r.json()
assert body["equal"] is False
fd = body["first_difference"]
assert fd["beat"] == {"n": 1, "d": 1}
assert fd["expected"]["event"]["pitch"] == 62
assert fd["handwritten"]["event"]["pitch"] == 63

# 3) 万亿级（2e12 事件）不展开，瞬时判定相等
trillion_req = {
    "expected_root": 2,
    "handwritten_root": 2,
    "nodes": [
        {"id": 0, "op": "phrase", "items": [note(60), note(62)]},
        {"id": 1, "op": "repeat", "child": 0, "count": 1_000_000},
        {"id": 2, "op": "repeat", "child": 1, "count": 1_000_000},
    ],
}
r = post(trillion_req)
assert r.status_code == 200, r.text
body = r.json()
assert body["equal"] is True
assert body["event_count"] == 2_000_000_000_000

# 4) 非法连音 → 整单 422，带 JSON 路径
bad_req = {
    "expected_root": 0,
    "handwritten_root": 0,
    "nodes": [{"id": 0, "op": "phrase",
               "items": [note(60, tie=True), note(63)]}],
}
r = post(bad_req)
assert r.status_code == 422, r.text
assert r.json()["detail"][0]["loc"] == "/nodes/0/items/1"

print("冒烟请求全部通过")
PY

echo "verify 完成"
