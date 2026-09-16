# 旋律变奏卡比对 API（纯后端）

面向 8–11 岁小朋友的**离散乐句**变奏卡场景：孩子把自编旋律做成「移调 / 镜像 /
倒放 / 时值拉伸 / 反复 / 顺接」的变奏卡送给同伴。家长不需要录音，只要把**基础
乐句**和**无环表达式 DAG** 提交给本服务，即可在不展开（repeat_count 允许到
10 亿、逻辑事件数可达一万亿）的前提下，用精确整数与分数压缩求值，指出手抄稿与
预期稿**第一处不同的精确拍点**以及两侧当时的事件/提前结束状态与来源链。

本服务**只处理离散乐句**：不采集、不播放、不生成任何音频；全程不使用浮点。

---

## 目录

- [运行方式](#运行方式)
- [接口](#接口)
- [数据模型](#数据模型)
- [音乐语义（规范化与变换）](#音乐语义规范化与变换)
- [第一处差异与来源链](#第一处差异与来源链)
- [请求示例](#请求示例)
- [错误处理（422）](#错误处理422)
- [压缩求值与复杂度](#压缩求值与复杂度)
- [测试](#测试)
- [目录结构](#目录结构)

---

## 运行方式

需要 Docker 与 Docker Compose（v2）。

```bash
# 构建镜像
docker compose build

# 仅启动长期 API（默认宿主端口 8000；可用 API_PORT 改写）
API_PORT=9000 docker compose up -d api

# 运行一次性 verify 服务：执行测试套件 + 向实际运行的分析接口发起冒烟请求后退出
docker compose up --build verify
```

- **api**：唯一长期运行的服务（uvicorn + FastAPI）。容器内固定监听 `8000`，
  宿主端口由环境变量 `API_PORT` 控制（默认 `8000`），即
  `${API_PORT:-8000}:8000`。
- **verify**：一次性服务。它依赖 api 的健康检查，先运行 `pytest`，再对
  `http://api:8000` 的真实 `/analyze` 接口发起 4 个冒烟请求（相等、第一差异、
  2 万亿事件压缩判定、非法连音 422），全部成功后退出，`restart: "no"`。

本地（无 Docker）开发：

```bash
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-test.txt
uvicorn app.main:app --reload
pytest
```

健康检查：`GET /health` → `{"status":"ok"}`。

---

## 接口

### `POST /analyze`

请求体：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| `expected_root` | 非负整数 | 预期（基础/标准）表达式根节点编号 |
| `handwritten_root` | 非负整数 | 手抄表达式根节点编号 |
| `nodes` | 节点数组 | 无环表达式 DAG，节点编号为**唯一非负整数**，至多 2000 个 |

两个根可以引用同一个节点表中的任意已定义节点（允许共享子图）。

响应 `200`：

```json
{
  "equal": false,
  "event_count": 3,
  "total_duration": {"n": 5, "d": 2},
  "first_difference": {
    "beat": {"n": 1, "d": 2},
    "expected": {
      "status": "event",
      "event": {"kind": "note", "pitch": 62, "duration": {"n": 1, "d": 1}},
      "source_chain": [{"node_id": 0, "repeat_iteration": null, "item_index": 1}]
    },
    "handwritten": {
      "status": "event",
      "event": {"kind": "note", "pitch": 63, "duration": {"n": 1, "d": 1}},
      "source_chain": [{"node_id": 1, "repeat_iteration": null, "item_index": 1}]
    }
  }
}
```

- `equal`：两侧规范事件序列（起音、音高、休止、时值）是否完全一致。
- `event_count` / `total_duration`：**预期根**的规范事件数与总时长（分数 `n/d`）。
- `first_difference`：第一处差异；`equal=true` 时为 `null`。
  - `beat`：从零开始的最早**精确**拍点（分数，不约分以外的任何近似）。
  - `expected` / `handwritten`：在该拍点两侧的当前视图。
    - `status="event"`：给出 `event`（`note` 带 `pitch`，`rest` 的 `pitch=null`）
      与 `source_chain`；
    - `status="ended"`：该侧已提前结束，`event=null`、`source_chain=[]`。

所有有理数都以 `{"n": 分子, "d": 分母}` 表示，`d` 省略时为 1；响应中的分数为
既约分数。**任何字段都不是浮点。**

---

## 数据模型

### 基础乐句节点 `phrase`

```json
{"id": 0, "op": "phrase", "items": [ ... ]}
```

`items` 中的基础项：

- 音符（起音）：
  ```json
  {"type": "note", "pitch": 60, "duration": {"n": 1, "d": 2}, "tie_next": false}
  ```
  - `pitch`：MIDI 音高整数，范围 **0–127**；
  - `duration`：**已约分**的正有理数（仅做约分，不要求调用方约分）；
  - `tie_next`：可选，默认 `false`。
- 休止：
  ```json
  {"type": "rest", "duration": {"n": 1}}
  ```

### 表达式节点

| `op` | 字段 | 语义 |
| --- | --- | --- |
| `concat` | `children: int[]` | 按顺序顺接若干子表达式 |
| `repeat` | `child: int, count: int` | 把子表达式整体重复 `count` 次；`count` ∈ [0, 10⁹] |
| `transpose` | `child, k: int` | 每个音高 `p → p + k`（休止不变） |
| `pitch_mirror` | `child, axis: 分数` | 每个音高 `p → 2·axis − p` |
| `reverse` | `child` | 反转**规范事件序列**的顺序（时值不反号） |
| `stretch` | `child, q: 正分数` | 每个事件时值 `d → d·q`（音高不变） |

未知 `op`、多余字段、错误类型一律 422。

---

## 音乐语义（规范化与变换）

所有变换都作用于**规范事件序列**。规范规则：

1. **连音（tie）规范化为一次起音**：`tie_next=true` 的音符只能连接**紧邻**的
   **同音高**音符；连音链上各音符时值相加，形成一个起音事件。连音到不同音高、
   连音到休止，或在链尾再遇异音，都是**非法连音**（422）。
2. **未连音的相邻同音必须保留两次起音**（不会被合并）。
3. **连续休止合并**为一个休止事件，时值相加。这既发生在 `phrase` 内部，也发生
   在 `concat` 顺接边界与 `repeat` 副本边界。休止合并不改变总时长，只减少事件数。
4. `repeat` 的 `count=0`（或子表达式为空）产生**空序列**；空部分在 `concat`
   中被忽略。

**音高范围**：`transpose(k)` 与 `pitch_mirror(axis)` 求值后，只要任一音高落到
MIDI 0–127 之外即为**语义错误（422）**（即使该事件理论上位于某次重复中也拒绝整
单，因为重复是确定性整体）。

- 镜像要把整数音高映射到整数音高，因此 `axis` 必须是**整数或半整数**
  （既约分母只能是 1 或 2），否则 422。例：`axis=121/2` 时
  `60 → 61`、`61 → 60`。
- 时值用 `fractions.Fraction` 精确计算；`stretch(q)` 的 `q` 必须为正。

---

## 第一处差异与来源链

比对按规范事件序列逐事件进行，同时维护两侧的累计拍点：

- 两侧当前事件的「音高（含休止与否）/ 时值」任一不同，即在该事件**起点拍点**
  报告；
- 一侧事件数先耗尽，则在该侧结束拍点报告，另一侧 `status="event"`、结束侧
  `status="ended"`；
- 否则 `equal=true`。

**来源链 `source_chain`** 自根向叶记录事件是如何沿 DAG 产生的，每一步包含：

- `node_id`：经过的 DAG 节点；
- `repeat_iteration`：经过 `repeat` 节点时的**零基**迭代（副本）号，否则 `null`；
- `item_index`：落到 `phrase` 基础项时的**零基基础项索引**，否则 `null`。

当一次连音或一次休止合并把多个贡献项归并为一个事件时，其来源取**演奏顺序最早**
的贡献项（例如 repeat 副本边界合并出的休止，取较早副本的尾部休止；跨 concat
合并的休止，取顺接顺序更靠前的部分）。

「同拍有多个候选」时的确定性排序为：**先预期侧，再次手抄侧，最后按节点编号
数值升序**。

---

## 请求示例

### 1）相等：连音 + 休止合并，与等价基础乐句一致

```bash
curl -s localhost:8000/analyze -H 'content-type: application/json' -d '{
  "expected_root": 0,
  "handwritten_root": 1,
  "nodes": [
    {"id": 0, "op": "phrase", "items": [
      {"type":"note","pitch":60,"duration":{"n":1,"d":2},"tie_next":true},
      {"type":"note","pitch":60,"duration":{"n":1,"d":2}},
      {"type":"rest","duration":{"n":1}},
      {"type":"rest","duration":{"n":1}}]},
    {"id": 1, "op": "phrase", "items": [
      {"type":"note","pitch":60,"duration":{"n":1}},
      {"type":"rest","duration":{"n":2}}]}
  ]
}'
```

### 2）变换抵消：+12 再 −12；镜像两次；拉伸 3/2 再 2/3；倒放两次

这些成对变换都应判定 `equal=true`（测试中逐一覆盖）。

### 3）跨拼接休止合并

`[60, 休止1] ⧺ [休止2, 64]` 规范为 `[60(1), 休止(3), 64(1)]`，边界两个休止合并。

### 4）零次反复

`repeat(child, 0)` 为空序列；与空 `phrase` 等价。

### 5）万亿级展开（不物化）

```json
{
  "expected_root": 2,
  "handwritten_root": 2,
  "nodes": [
    {"id": 0, "op": "phrase", "items": [
      {"type":"note","pitch":60,"duration":{"n":1}},
      {"type":"note","pitch":62,"duration":{"n":1}}]},
    {"id": 1, "op": "repeat", "child": 0, "count": 1000000},
    {"id": 2, "op": "repeat", "child": 1, "count": 1000000}
  ]
}
```

逻辑事件数 = 2 × 10⁶ × 10⁶ = **2,000,000,000,000**，服务在毫秒级判定相等，
内存占用与该数字无关。

### 6）端点差异（手抄提前结束）

预期 3 个音、手抄 2 个音 → 在拍点 `2` 报告：`expected` 为第三个音，
`handwritten.status="ended"`。

---

## 错误处理（422）

以下情况**整单拒绝**为 HTTP `422`，响应体 `detail` 为错误数组，每项的 `loc`
是指向请求体的 **JSON Pointer**（如 `/nodes/3/children/1`、
`/nodes/0/items/2`、`/expected_root`），绝不返回任何「部分等价」结论：

- 成环引用（含自环）、子节点/根节点未知、节点编号重复或为负；
- 节点数超过 2000、单个 `phrase` 项数超过上限；
- 坏分数（分母 ≤ 0、`stretch` 倍率非正、时值非正）；
- 非法连音（连到异音/休止）；
- `transpose` / `pitch_mirror` 使音高越出 0–127；镜像轴分母不是 1/2；
- 未知操作、多余/缺失字段、类型错误；
- `repeat.count` 超出 [0, 10⁹]；
- **规模越界**：任何节点的规范事件数超过 10¹⁵。

结构错误由 Pydantic v2 判别式联合模型拦截；语义错误（环、越界、非法连音、规模
等）由编译器抛出并统一转成相同形状的 422。

---

## 压缩求值与复杂度

**绝不完整展开 repeat。** 核心表示（`app/core/terms.py`）为每种节点保存结构化
元数据：事件数、总时长、音高范围、首/尾是否休止及时值、concat 的编译槽、repeat
的副本长度与首尾休止信息。时值全部是 `fractions.Fraction`，计数全部是 Python
任意精度大整数。

- **编译**：O(V + E + 基础项总数)。concat 预编译成 `body`（子表达式的显式事件
  区间）与 `token`（跨边界合并出的单个休止）槽序列，合并在编译期一次完成。
- **取任意事件 / 拍点 / 来源链**：沿 DAG 深度用纯算术定位（repeat 用
  `divmod`，concat 用槽前缀二分），O(节点深度)，与 `count` 大小无关。
- **比对（`app/core/compare.py`）**：
  1. 两侧游标顺序产出事件并比较；
  2. **算术块跳转**：当两侧同时位于某个 repeat 的周期边界（普通 repeat 的副本
     起点，或首尾休止 repeat 的内部周期）时，取两侧副本长度的最小公倍数为联合
     周期，先实走一个联合周期确认相等，再一次跳过「剩余的整周期」，因此重复
     10⁹ 次、嵌套出一万亿事件也只走 O(结构规模) 步；
  3. 签名周期检测作为一般兜底（合并休止 token、任意周期性结构）；
  4. 始终保留至少一个完整周期实走到序列尾，因此**端点与末尾内部差异都被精确
     捕获**，不会被整块跳过。
- **空间**：O(V + E + 基础项总数)，与逻辑展开事件数无关。

正确性由差分测试保证：随机生成的合法 DAG（含连音、休止合并、全部变换、嵌套
repeat/concat）同时交给「朴素完整展开参考实现」与压缩实现，逐事件、逐拍点、逐
来源链比对；另有大计数（最高数亿次重复 / 2 万亿事件）专项与任意位置单处变异的
精确定位测试。

---

## 测试

```bash
pip install -r requirements-test.txt
pytest                      # 全部 188+ 项
```

覆盖：变换抵消、零次反复、跨拼接休止合并（含连续纯休止部分）、同音再起（未连音
保留两次起音）、非法连音、连音来源、repeat 边界休止合并及来源迭代号、镜像轴
（整数/半整数/非法分母）、移调与镜像越界、成环/未知节点/重复 id/未知操作/坏
分数/规模越界等 422、同拍多候选来源排序、端点差异（任一侧提前结束）、万亿级
相等与**末尾内部差异**的秒级精确定位。测试中不使用浮点近似、不使用预制答案、
不存在占位实现。

---

## 目录结构

```
.
├── app/
│   ├── main.py            # FastAPI 路由、422 统一处理、响应装配
│   ├── models.py          # Pydantic v2 请求/响应模型与规模常量
│   ├── serialization.py   # Fraction / 事件 / 来源链序列化
│   └── core/
│       ├── errors.py      # SemanticError（带 JSON Pointer）
│       ├── events.py      # 规范事件与来源链单步
│       ├── terms.py       # 压缩 Term：concat 槽、repeat 算术映射、拍点
│       ├── compiler.py    # DAG 校验、规范化、越界、规模、压缩编译
│       ├── cursor.py      # 压缩游标：取事件/拍点/来源链/周期边界
│       └── compare.py     # 逐事件比对 + 算术块跳转 + 签名兜底
├── tests/                 # 固定语义 + 随机差分 + 大块跳转 + 对抗变异
├── scripts/verify.sh      # verify 一次性服务入口
├── Dockerfile
├── docker-compose.yml     # api（长期）+ verify（一次性）
├── requirements.txt
├── requirements-test.txt
└── pytest.ini
```
