"""请求 / 响应 Pydantic 模型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator

BeatStatus = Literal["early", "on_time", "late"]


class AlignmentParamsPayload(BaseModel):
    band_width: int = Field(
        default=3, ge=1, le=10000,
        description="Sakoe-Chiba 带宽：允许 |参考索引-演奏索引| 偏离的最大拍数",
    )
    skip_penalty: float | None = Field(
        default=None, ge=0,
        description="漏一拍的代价（秒²）；默认 (0.5×中位拍间隔)²",
    )
    insert_penalty: float | None = Field(
        default=None, ge=0,
        description="插入一拍的代价（秒²）；默认与漏拍代价相同",
    )
    on_time_threshold: float = Field(
        default=0.03, gt=0,
        description="|偏差|≤该秒数判定为准时",
    )


class JobCreate(BaseModel):
    name: str | None = Field(default=None, max_length=200, description="任务名称")
    reference_beats: list[float] = Field(
        ..., min_length=1, description="参考节拍网格时间（秒），需严格递增"
    )
    performance_onsets: list[float] = Field(
        ..., min_length=1, description="演奏音符起始时间（秒），需严格递增"
    )
    params: AlignmentParamsPayload = Field(default_factory=AlignmentParamsPayload)

    @field_validator("reference_beats", "performance_onsets")
    @classmethod
    def _strictly_increasing(cls, v: list[float]) -> list[float]:
        # 允许负值（演奏音可能略早于第 0 拍，即前摇），但必须严格递增
        if any(v[i] <= v[i - 1] for i in range(1, len(v))):
            raise ValueError("时间序列必须严格递增")
        return v


class BeatPair(BaseModel):
    ref_index: int
    perf_index: int
    ref_time: float
    perf_time: float
    deviation: float
    status: BeatStatus


class MissedBeat(BaseModel):
    ref_index: int
    ref_time: float


class InsertedBeat(BaseModel):
    perf_index: int
    perf_time: float


class DeviationSegment(BaseModel):
    segment_index: int
    ref_start_index: int
    ref_end_index: int
    ref_start_time: float
    ref_end_time: float
    beat_count: int
    min_deviation: float
    max_deviation: float
    mean_deviation: float
    beats: list[BeatPair]


class SignedInterval(BaseModel):
    segment_index: int
    status: BeatStatus
    ref_start_index: int
    ref_end_index: int
    ref_start_time: float
    ref_end_time: float
    beat_count: int
    min_deviation: float
    max_deviation: float
    mean_deviation: float


class AlignmentResult(BaseModel):
    parameters: dict[str, Any]
    matched_beats: list[BeatPair]
    missed_beats: list[MissedBeat]
    inserted_beats: list[InsertedBeat]
    deviation_segments: list[DeviationSegment]
    signed_intervals: list[SignedInterval]
    statistics: dict[str, Any]


class JobSummary(BaseModel):
    id: int
    name: str | None
    status: str
    created_at: str
    aligned_at: str | None
    ref_beat_count: int
    perf_onset_count: int


class JobOut(BaseModel):
    id: int
    name: str | None
    reference_beats: list[float]
    performance_onsets: list[float]
    params: dict[str, Any]
    status: str
    error: str | None
    created_at: str
    aligned_at: str | None
    result: AlignmentResult | None = None
