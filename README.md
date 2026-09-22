# 多轨录音节拍对齐 API（FastAPI + SQLite）

客户端提交**参考节拍网格**（reference beat grid）与某条演奏轨的**音符起始时间**
（note onsets）。服务端使用**带宽约束的动态时间规整（Sakoe-Chiba Band DTW）**
在带状窗口内寻找最优对齐路径，显式处理：

- **漏拍（missed beat）**：参考网格里有、演奏中缺失的拍；
- **抢拍（early）/ 拖拍（late）**：演奏相对参考网格的连续时间偏差；
- **插入拍（inserted beat）**：演奏中多出来、参考网格里没有的音。

回溯最优路径后输出：参考拍 ↔ 演奏音的**一一对应映射**、漏拍 / 插入拍清单、
按连续段组织的**偏差区间**及统计指标。结果持久化到 SQLite，可反复查询。

创建任务时还可提供若干**锚点（anchors）硬约束**——明确知道某参考拍对应某
演奏音时，对齐必须经过这些对应点：DTW 被分段限制在相邻锚点之间独立求解，
再合并为完整路径，锚点处一定是一一匹配（不会被判成漏拍 / 插入拍）。

## 目录结构

```
.
├── app/
│   ├── main.py          # FastAPI 路由
│   ├── alignment.py     # 带宽约束 DTW 核心算法（纯标准库）
│   ├── schemas.py       # Pydantic 请求/响应模型
│   ├── database.py      # SQLite 连接与建表
│   └── crud.py          # 任务读写
├── tests/               # unittest 测试（算法 20 个 + 接口 14 个）
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```

## 一键启动（Docker Compose）

```bash
docker compose up --build
```

- 服务端口：**`8000`**（容器内 8000，映射到宿主机同端口）
- 交互式文档（Swagger UI）：<http://localhost:8000/docs>
- 只读文档（ReDoc）：<http://localhost:8000/redoc>
- 健康检查：`GET http://localhost:8000/health`
- SQLite 数据文件：容器内 `/data/alignment.db`，由命名卷 `beat-data` 持久化

停止：

```bash
docker compose down          # 保留数据
docker compose down -v       # 同时删除数据库卷
```

## 本地（无 Docker）运行

需要 Python 3.11：

```bash
pip install -r requirements.txt
ALIGN_DB_PATH=./data/alignment.db uvicorn app.main:app --host 0.0.0.0 --port 8000
```

运行测试：

```bash
PYTHONPATH=. python -m unittest discover -s tests -v
```

## API 一览

| 方法   | 路径                       | 说明                                   |
| ------ | -------------------------- | -------------------------------------- |
| POST   | `/jobs`                    | 创建对齐任务（状态 `pending`）         |
| POST   | `/jobs/{id}/align`         | 执行 DTW 对齐并保存结果                |
| GET    | `/jobs/{id}`               | 查询任务（含完整输入与结果）           |
| GET    | `/jobs/{id}/result`        | 只查询对齐结果                         |
| GET    | `/jobs`                    | 任务列表（摘要，按创建时间倒序）       |
| GET    | `/health`                  | 健康检查                               |

`POST /jobs/{id}/align` 已完成的任务默认直接返回原结果；加 `?force=true`
可强制重新对齐。

### 请求体（`POST /jobs`）

```json
{
  "name": "take-01",
  "reference_beats": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5],
  "performance_onsets": [-0.02, 0.47, 1.53, 1.76, 2.04, 2.52],
  "params": {
    "band_width": 2,
    "skip_penalty": null,
    "insert_penalty": null,
    "on_time_threshold": 0.03
  },
  "anchors": [
    {"ref_index": 0, "perf_index": 0},
    {"ref_index": 5, "perf_index": 5}
  ]
}
```

- `reference_beats` / `performance_onsets`：单位秒，必须**严格递增**
  （允许略小于 0 的前摇时间，例如演奏音比第 0 拍早几十毫秒）。
- `params.band_width`：Sakoe-Chiba 带宽（拍数，默认 3）。要求
  `|len(ref) − len(perf)| ≤ band_width`，否则创建时返回 `400`。
  带宽越小越保守（只允许局部错位），越大越能容忍连续漏拍 / 插入拍。
- `params.skip_penalty` / `insert_penalty`：漏 / 插一拍的代价（**秒²**）。
  默认 `(0.5 × 参考网格中位拍间隔)²`（本例拍间隔 0.5s ⇒ 0.0625）。
- `params.on_time_threshold`：`|偏差| ≤` 该秒数判定为 `on_time`（默认 0.03s）。
- `anchors`（可选，默认 `[]`）：锚点硬约束数组，每项为
  `{"ref_index": 参考拍索引, "perf_index": 演奏音索引}`（均 0 起）。校验规则：
  - 两侧索引都必须**严格递增**（不允许两个锚点共用同一参考拍 / 同一演奏音）；
  - 不得越界：`0 ≤ ref_index < len(reference_beats)`、
    `0 ≤ perf_index < len(performance_onsets)`；
  - 每个锚点必须在带宽内可达：`|ref_index − perf_index| ≤ band_width`。

  以上任一不满足，创建任务返回 `400`（错误信息指明第几个锚点及原因）；
  索引为负数等类型级错误返回 `422`。执行对齐时会再次校验并返回清晰的
  `400`，同时把任务置为 `error`。

偏差符号约定：`deviation = perf_time − ref_time`，负值=**抢拍 early**，
正值=**拖拍 late**。

### 锚点如何约束对齐

锚点把对齐路径（DTW 网格上 `(0,0) → (n,m)` 的单调路径）强制分割为

```
起点 → 锚点 1 → 锚点 2 → … → 锚点 k → 终点
```

若干段，各段**仍在同一条 Sakoe-Chiba 带（全局坐标 |i−j| ≤ band_width）内
独立做带宽约束 DTW**，然后按序拼接为完整路径；锚点所在格点只允许以对角
（匹配）步到达，因此每个锚点保证是一次真实的一一匹配。漏拍 / 插入拍只能
发生在相邻锚点之间的段内。结果 `result.anchors` 回显本次实际采用的锚点
（含两侧时间与偏差），任务对象上也有提交时的 `anchors` 字段。

典型用途：人工标注或由另一条已对齐轨道确定了少量确定性对应（例如开头、
结尾、明显的结构点），希望避免 DTW 在这些位置发生“漂移式错配”。

## 示例请求（curl 完整流程）

```bash
# 1) 创建任务（anchors 可省略）
curl -s -X POST http://localhost:8000/jobs \
  -H 'Content-Type: application/json' \
  -d '{
    "name": "take-01",
    "reference_beats": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5],
    "performance_onsets": [-0.02, 0.47, 1.53, 1.76, 2.04, 2.52],
    "params": {"band_width": 2},
    "anchors": [{"ref_index": 0, "perf_index": 0},
                {"ref_index": 5, "perf_index": 5}]
  }'
# => 201，记录返回的 "id"（下文以 $ID 表示）

# 2) 执行对齐
curl -s -X POST http://localhost:8000/jobs/$ID/align

# 3) 查询结果
curl -s http://localhost:8000/jobs/$ID/result

# 4) 列出全部任务
curl -s http://localhost:8000/jobs
```

Python 示例：

```python
import requests

BASE = "http://localhost:8000"

job = requests.post(f"{BASE}/jobs", json={
    "name": "take-01",
    "reference_beats": [0.0, 0.5, 1.0, 1.5, 2.0, 2.5],
    "performance_onsets": [-0.02, 0.47, 1.53, 1.76, 2.04, 2.52],
    "params": {"band_width": 2},
    "anchors": [{"ref_index": 0, "perf_index": 0}],  # 可选硬约束
}).json()

result = requests.post(f"{BASE}/jobs/{job['id']}/align").json()["result"]

for b in result["matched_beats"]:
    print(b["ref_index"], "->", b["perf_index"],
          f"偏差 {b['deviation']:+.2f}s ({b['status']})")
print("漏拍:", [b["ref_index"] for b in result["missed_beats"]])
print("插入拍:", [b["perf_index"] for b in result["inserted_beats"]])
```

## 结果结构（节选）

上例中参考拍 `1.0s`（索引 2）被漏，演奏在 `1.76s`（索引 3）插入了一个音：

```json
{
  "anchors": [
    {"ref_index": 0, "perf_index": 0, "ref_time": 0.0, "perf_time": -0.02, "deviation": -0.02},
    {"ref_index": 5, "perf_index": 5, "ref_time": 2.5, "perf_time": 2.52, "deviation": 0.02}
  ],
  "matched_beats": [
    {"ref_index": 0, "perf_index": 0, "ref_time": 0.0,  "perf_time": -0.02, "deviation": -0.02, "status": "on_time"},
    {"ref_index": 1, "perf_index": 1, "ref_time": 0.5,  "perf_time": 0.47,  "deviation": -0.03, "status": "early"},
    {"ref_index": 3, "perf_index": 2, "ref_time": 1.5,  "perf_time": 1.53,  "deviation": 0.03,  "status": "late"},
    {"ref_index": 4, "perf_index": 4, "ref_time": 2.0,  "perf_time": 2.04,  "deviation": 0.04,  "status": "late"},
    {"ref_index": 5, "perf_index": 5, "ref_time": 2.5,  "perf_time": 2.52,  "deviation": 0.02,  "status": "on_time"}
  ],
  "missed_beats":   [{"ref_index": 2, "ref_time": 1.0}],
  "inserted_beats": [{"perf_index": 3, "perf_time": 1.76}],
  "deviation_segments": [ /* 参考索引连续的最大匹配段，含段内逐拍偏差 */ ],
  "signed_intervals":   [ /* 段内按 抢/准/拖 归并的同向连续区间 */ ],
  "statistics": {
    "ref_beat_count": 6, "perf_onset_count": 6,
    "matched_count": 5, "missed_count": 1, "inserted_count": 1,
    "early_count": 1, "on_time_count": 2, "late_count": 2,
    "mean_deviation": 0.008, "mean_absolute_deviation": 0.028,
    "rms_deviation": 0.028983, "max_absolute_deviation": 0.04,
    "total_cost": 0.1292, "normalized_cost": 0.018457
  }
}
```

- `anchors`：本次对齐实际采用的锚点（无锚点时为 `[]`），含两侧索引、
  时间与偏差；每个锚点都一定出现在 `matched_beats` 中。
- `matched_beats`：一一对应映射，含两侧索引、时间、偏差（秒）与状态。
- `missed_beats` / `inserted_beats`：未配对的参考拍 / 演奏音。
- `deviation_segments`：按**参考拍索引连续**切出的最大匹配段（漏拍 /
  插入拍造成缺口时另起一段）。段内偏差是关于参考时间的连续分段线性函数，
  可直接用于绘制偏差曲线或做时间拉伸补偿；含段的起止时间、逐拍偏差及
  min/max/mean。
- `signed_intervals`：在每个连续段内再按 `early/on_time/late` 归并，得到
  **同向连续偏差区间**（例如“第 3–4 拍连续拖拍，平均 +0.035s”）。
- `statistics`：配对数量、抢/准/拖计数、平均偏差、平均绝对偏差、RMS、
  最大绝对偏差、路径总代价与按路径长度归一化的代价。

## 算法说明

设参考拍 `r₀..rₙ₋₁`、演奏音 `p₀..pₘ₋₁`，代价矩阵单元
`D(i,j)` 表示从起点对齐到“参考前 i 拍 / 演奏前 j 音”的最小代价：

```
                 ⎧ D(i−1,j−1) + (p[j−1] − r[i−1])²   匹配（对角）
D(i,j) = min of  ⎨ D(i−1,j)   + skip_penalty          漏拍（水平）
                 ⎩ D(i,j−1)   + insert_penalty         插入拍（垂直）
```

- 只在 Sakoe-Chiba 带 `|i − j| ≤ band_width` 内展开（端点固定为
  `(0,0) → (n,m)`），时间复杂度约 O((n+m)·band_width)，空间 O(n·m)。
- 匹配代价用**时间偏差平方**；漏 / 插代价为常数惩罚，因此只有在时间偏差
  确实比“判为漏拍 + 相邻配对”更划算时才会选择漏 / 插，避免误配。
- 代价同以“秒²”为单位；默认惩罚取 `(0.5 × 中位拍间隔)²`。
- 回填时记录回溯指针（对角/水平/垂直），还原唯一最优路径；代价相同优先
  取对角匹配，保证结果确定。
- 2000 拍、带宽 13 的典型输入在普通笔记本上约 0.3s、50MB 内存。

### 锚点约束下的分段 DTW

设锚点为 `(a₁,b₁), …, (aₖ,bₖ)`，在网格上加入虚拟边界起点 `(0,0)` 与
终点 `(n,m)`，对每对相邻边界格点 `(i₀,j₀) → (i₁,j₁)` 分别求解一个
Sakoe-Chiba 带子问题（带判定沿用**全局坐标** `|i−j| ≤ band_width`），
段代价相加、回溯路径按序拼接：

- 锚点 `(a,b)` 对应的格点是 `(a+1, b+1)`；该格点**禁止以水平（漏拍）或
  垂直（插入拍）步到达**，只允许对角匹配步，确保锚点被真正一一匹配；
- 每段仍是标准三转移 DTW（对角匹配 / 水平漏拍 / 垂直插入拍），段间互不
  影响，漏拍 / 插入拍只能落在段内；
- 因带判定基于全局索引，拼接后的完整路径与“在同一张全局 DP 表上把锚点
  状态强制可达”等价：是一条位于原 Sakoe-Chiba 带内、经过全部锚点的
  最优路径。
- 校验在创建与执行对齐两处进行：索引严格递增、不越界、
  `|ref_index − perf_index| ≤ band_width`（带宽不可达直接拒绝）。

## 状态码

- `201` 任务创建成功；`200` 对齐 / 查询成功
- `400` 带宽不足无法对齐（含 `|n−m| > band_width` 预检）、惩罚参数非法，
  或锚点不满足严格递增 / 越界 / 带宽不可达（错误信息指明第几个锚点）
- `404` 任务不存在
- `409` 对尚未对齐（`pending`）的任务调用 `/result`
- `422` 请求体校验失败（空序列、非递增、锚点索引为负数等类型错误）
