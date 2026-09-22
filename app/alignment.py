"""带宽约束的动态时间规整（DTW）节拍对齐核心算法。

参考节拍网格 ref 与演奏起始时间 perf 两个单调序列在 Sakoe-Chiba
带状窗口内做对齐，显式建模三种事件：

* match    —— 参考拍 i 与演奏拍 j 一一对应，代价为时间偏差平方；
* skip     —— 漏拍（演奏缺失参考拍 i），代价为惩罚项；
* insert   —— 插入拍（多出来的演奏音 j），代价为惩罚项。

回溯后输出一一对应映射、漏拍 / 插入拍列表、连续偏差区间及统计指标。
"""

from __future__ import annotations

import math
from array import array
from dataclasses import dataclass
from typing import Any, Sequence

# 回溯指针：1=对角(匹配) 2=水平(漏拍) 3=垂直(插入拍)
_PTR_MATCH = 1
_PTR_SKIP = 2
_PTR_INSERT = 3


class AlignmentError(ValueError):
    """输入数据无法在给定带宽内完成对齐。"""


@dataclass(frozen=True)
class AlignmentParams:
    """对齐参数。

    band_width: Sakoe-Chiba 带宽，允许 |i-j| > band_width 的路径被禁止，
        同时要求 |len(ref)-len(perf)| <= band_width，否则无法对齐。
    skip_penalty: 漏一拍的代价（秒的平方）；None 表示取
        0.5 * 中位参考拍间隔 的平方。
    insert_penalty: 插入一拍的代价（秒的平方）；None 时与漏拍相同。
    on_time_threshold: |偏差| 小于等于该秒数视为“准时”（仅用于分类）。
    """

    band_width: int = 3
    skip_penalty: float | None = None
    insert_penalty: float | None = None
    on_time_threshold: float = 0.03

    @classmethod
    def from_payload(cls, payload: Any | None) -> "AlignmentParams":
        if payload is None:
            return cls()
        return cls(
            band_width=int(payload.band_width),
            skip_penalty=payload.skip_penalty,
            insert_penalty=payload.insert_penalty,
            on_time_threshold=float(payload.on_time_threshold),
        )


def _median(values: Sequence[float]) -> float:
    s = sorted(values)
    n = len(s)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2:
        return s[mid]
    return 0.5 * (s[mid - 1] + s[mid])


def _default_penalty(ref: Sequence[float]) -> float:
    """默认漏拍 / 插入代价：(0.5 × 中位参考拍间隔)^2。"""
    if len(ref) < 2:
        return 0.25
    gaps = [ref[i] - ref[i - 1] for i in range(1, len(ref))]
    half = 0.5 * _median(gaps)
    return half * half


def _classify(dev: float, threshold: float) -> str:
    if dev <= -threshold:
        return "early"  # 抢拍（演奏早于参考）
    if dev >= threshold:
        return "late"  # 拖拍（演奏晚于参考）
    return "on_time"  # 准时


def align_beats(
    ref: Sequence[float],
    perf: Sequence[float],
    params: AlignmentParams | None = None,
) -> dict[str, Any]:
    """执行带宽约束 DTW 对齐，返回完整结果字典。"""
    params = params or AlignmentParams()
    n = len(ref)
    m = len(perf)
    if n == 0 or m == 0:
        raise AlignmentError("参考网格与演奏起始均不能为空")
    if abs(n - m) > params.band_width:
        raise AlignmentError(
            f"序列长度差 {abs(n - m)} 超过带宽 {params.band_width}，"
            "请增大 band_width 或检查输入"
        )

    skip_cost = (
        params.skip_penalty if params.skip_penalty is not None else _default_penalty(ref)
    )
    insert_cost = (
        params.insert_penalty
        if params.insert_penalty is not None
        else skip_cost
    )
    if skip_cost < 0 or insert_cost < 0:
        raise AlignmentError("惩罚代价不能为负")

    width = params.band_width
    size = (n + 1) * (m + 1)

    # 一维展平的代价矩阵与回溯指针；用紧凑数组降低内存占用。
    dp = array("d", [math.inf]) * size
    ptr = array("b", bytes(size))
    dp[0] = 0.0

    def idx(i: int, j: int) -> int:
        return i * (m + 1) + j

    def in_band(a: int, b: int) -> bool:
        return -width <= a - b <= width

    # 正向 DP：固定端点 (0,0)->(n,m)，只在带宽内展开。
    for i in range(0, n + 1):
        j_lo = max(0, i - width)
        j_hi = min(m, i + width)
        for j in range(j_lo, j_hi + 1):
            if i == 0 and j == 0:
                continue
            cur = idx(i, j)
            best = math.inf
            best_ptr = 0

            # 对角：ref[i-1] <-> perf[j-1]（匹配）
            if i > 0 and j > 0 and in_band(i - 1, j - 1):
                d = perf[j - 1] - ref[i - 1]
                cand = dp[idx(i - 1, j - 1)] + d * d
                if cand < best:
                    best, best_ptr = cand, _PTR_MATCH
            # 水平：跳过参考拍 i-1（漏拍）
            if i > 0 and in_band(i - 1, j):
                cand = dp[idx(i - 1, j)] + skip_cost
                if cand < best:
                    best, best_ptr = cand, _PTR_SKIP
            # 垂直：插入演奏拍 j-1（插入拍）
            if j > 0 and in_band(i, j - 1):
                cand = dp[idx(i, j - 1)] + insert_cost
                if cand < best:
                    best, best_ptr = cand, _PTR_INSERT

            if best_ptr:
                dp[cur] = best
                ptr[cur] = best_ptr

    total_cost = dp[idx(n, m)]
    if not math.isfinite(total_cost):
        raise AlignmentError("在给定带宽内不存在可行对齐路径，请增大 band_width")

    # 回溯
    pairs: list[tuple[int, int]] = []
    missed: list[int] = []
    extra: list[int] = []
    i, j = n, m
    while i > 0 or j > 0:
        p = ptr[idx(i, j)]
        if p == _PTR_MATCH:
            pairs.append((i - 1, j - 1))
            i -= 1
            j -= 1
        elif p == _PTR_SKIP:
            missed.append(i - 1)
            i -= 1
        elif p == _PTR_INSERT:
            extra.append(j - 1)
            j -= 1
        else:  # pragma: no cover - 理论不可达
            raise AlignmentError("回溯失败：遇到不可达状态")

    pairs.reverse()
    missed.sort()
    extra.sort()

    matched = [
        {
            "ref_index": ri,
            "perf_index": pj,
            "ref_time": round(ref[ri], 6),
            "perf_time": round(perf[pj], 6),
            "deviation": round(perf[pj] - ref[ri], 6),
            "status": _classify(perf[pj] - ref[ri], params.on_time_threshold),
        }
        for ri, pj in pairs
    ]

    missed_beats = [
        {"ref_index": ri, "ref_time": round(ref[ri], 6)} for ri in missed
    ]
    inserted_beats = [
        {"perf_index": pj, "perf_time": round(perf[pj], 6)} for pj in extra
    ]

    segments, signed_intervals = _build_intervals(matched, params.on_time_threshold)
    stats = _build_stats(
        matched, missed_beats, inserted_beats, total_cost,
        n, m, skip_cost, insert_cost,
    )

    return {
        "parameters": {
            "band_width": params.band_width,
            "skip_penalty": skip_cost,
            "insert_penalty": insert_cost,
            "on_time_threshold": params.on_time_threshold,
        },
        "matched_beats": matched,
        "missed_beats": missed_beats,
        "inserted_beats": inserted_beats,
        "deviation_segments": segments,
        "signed_intervals": signed_intervals,
        "statistics": stats,
    }


def _build_intervals(
    matched: list[dict[str, Any]], threshold: float
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """生成两类连续偏差区间。

    deviation_segments：按参考拍索引连续的最大匹配段。每段内偏差可视为
        关于参考时间的连续分段线性函数；漏拍 / 插入拍造成缺口时另起一段。
    signed_intervals：在连续段内再按偏差符号（抢/准/拖）归并，给出每个
        同向连续区间的时间跨度与偏差统计。
    """
    segments: list[dict[str, Any]] = []
    signed_intervals: list[dict[str, Any]] = []

    run: list[dict[str, Any]] = []

    def flush_run(run_beats: list[dict[str, Any]]) -> None:
        if not run_beats:
            return
        devs = [b["deviation"] for b in run_beats]
        seg_idx = len(segments)
        segment = {
            "segment_index": seg_idx,
            "ref_start_index": run_beats[0]["ref_index"],
            "ref_end_index": run_beats[-1]["ref_index"],
            "ref_start_time": run_beats[0]["ref_time"],
            "ref_end_time": run_beats[-1]["ref_time"],
            "beat_count": len(run_beats),
            "min_deviation": round(min(devs), 6),
            "max_deviation": round(max(devs), 6),
            "mean_deviation": round(sum(devs) / len(devs), 6),
            "beats": run_beats,
        }
        segments.append(segment)

        # 段内按 status 归并出同向连续区间
        group: list[dict[str, Any]] = []

        def flush_group(beats: list[dict[str, Any]]) -> None:
            if not beats:
                return
            g_devs = [b["deviation"] for b in beats]
            signed_intervals.append({
                "segment_index": seg_idx,
                "status": beats[0]["status"],
                "ref_start_index": beats[0]["ref_index"],
                "ref_end_index": beats[-1]["ref_index"],
                "ref_start_time": beats[0]["ref_time"],
                "ref_end_time": beats[-1]["ref_time"],
                "beat_count": len(beats),
                "min_deviation": round(min(g_devs), 6),
                "max_deviation": round(max(g_devs), 6),
                "mean_deviation": round(sum(g_devs) / len(g_devs), 6),
            })

        for beat in run_beats:
            if group and beat["status"] != group[-1]["status"]:
                flush_group(group)
                group = []
            group.append(beat)
        flush_group(group)

    for beat in matched:
        if run and beat["ref_index"] != run[-1]["ref_index"] + 1:
            flush_run(run)
            run = []
        run.append(beat)
    flush_run(run)

    return segments, signed_intervals


def _build_stats(
    matched: list[dict[str, Any]],
    missed: list[dict[str, Any]],
    inserted: list[dict[str, Any]],
    total_cost: float,
    n: int,
    m: int,
    skip_cost: float,
    insert_cost: float,
) -> dict[str, Any]:
    devs = [b["deviation"] for b in matched]
    if devs:
        mean = sum(devs) / len(devs)
        rms = math.sqrt(sum(d * d for d in devs) / len(devs))
        abs_mean = sum(abs(d) for d in devs) / len(devs)
        max_abs = max(abs(d) for d in devs)
    else:
        mean = rms = abs_mean = max_abs = 0.0

    early = sum(1 for b in matched if b["status"] == "early")
    late = sum(1 for b in matched if b["status"] == "late")
    on_time = sum(1 for b in matched if b["status"] == "on_time")

    path_len = len(matched) + len(missed) + len(inserted)
    normalized_cost = total_cost / path_len if path_len else total_cost

    return {
        "ref_beat_count": n,
        "perf_onset_count": m,
        "matched_count": len(matched),
        "missed_count": len(missed),
        "inserted_count": len(inserted),
        "early_count": early,
        "on_time_count": on_time,
        "late_count": late,
        "mean_deviation": round(mean, 6),
        "mean_absolute_deviation": round(abs_mean, 6),
        "rms_deviation": round(rms, 6),
        "max_absolute_deviation": round(max_abs, 6),
        "total_cost": round(total_cost, 6),
        "normalized_cost": round(normalized_cost, 6),
    }
